"""Interviewer TTS: Fish Audio cloud (live WebSocket) or a self-hosted fish-speech server.

Cloud protocol (docs.fish.audio/api-reference/endpoint/websocket/tts-live):
  client -> {"event":"start","request":{...}} , {"event":"text","text":...} , {"event":"flush"} , {"event":"stop"}
  server -> {"event":"audio","audio":<bytes>} , {"event":"finish","reason":...} , {"event":"error",...}

One socket per interviewer reply. `TTSStream.open()` is called as soon as the candidate stops talking so the
TLS + WS handshake overlaps with STT and the LLM's first token.

Self-hosted (TTS_PROVIDER=fish_local: github.com/fishaudio/fish-speech tools/api_server.py; TTS_PROVIDER=chatterbox:
infra/chatterbox/server.py, which speaks the same API) has no live socket:
POST /v1/tts with streaming=true returns a 44-byte WAV header then PCM16 per generated segment. `LocalTTSStream`
turns each chunk from the sentence chunker into one such request, run in order, so the next sentence is being
generated while the previous one plays.
"""

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable

import httpx
import ormsgpack
import websockets

from ..config import get_settings

log = logging.getLogger(__name__)
URL = "wss://api.fish.audio/v1/tts/live"

# S2 models take free-form [bracket] cues; keep them for audio, strip them for captions.
# A cue stands alone ("[chuckle] Fair enough"); brackets glued to a name are code (nums[i], arr[left]) and must survive.
TAG_RE = re.compile(r"(?<![\w\])])\[[a-z][a-z \-']{2,30}\]", re.I)
INDEX_RE = re.compile(r"(\w)\[([^\[\]]{1,30})\]")


def speakable(text: str) -> str:
    """What the TTS should say: code indexing read aloud the way people do ("nums at i minus 1")."""
    return INDEX_RE.sub(lambda m: f"{m.group(1)} at {m.group(2).replace('-', ' minus ').replace('+', ' plus ')}",
                        text)


def strip_tags(text: str) -> str:
    return re.sub(r"\s{2,}", " ", TAG_RE.sub("", text)).strip()


class TTSStream:
    def __init__(self, voice_id: str, on_audio: Callable[[bytes], Awaitable[None]]):
        self.voice_id = voice_id
        self.on_audio = on_audio
        self.ws: websockets.ClientConnection | None = None
        self._reader: asyncio.Task | None = None
        self._opened = asyncio.Event()
        self.first_audio_at: float | None = None
        self.first_text_at: float | None = None
        self.done = asyncio.Event()
        self.error: str | None = None
        self.closed = False
        self._odd: bytes = b""

    async def open(self) -> None:
        s = get_settings()
        if not s.fish_api_key:
            raise RuntimeError("FISH_API_KEY is not set")
        try:
            self.ws = await websockets.connect(
                URL,
                additional_headers={"Authorization": f"Bearer {s.fish_api_key}", "model": s.fish_tts_model},
                max_size=None,
                open_timeout=6,
                ping_interval=None,
            )
            req = {
                "text": "",
                "format": "pcm",
                "sample_rate": s.tts_sample_rate,
                "latency": s.tts_latency,
                "chunk_length": 100,
                "normalize": True,
                "prosody": {"speed": 1.0, "volume": 0},
                "temperature": 0.7,
                "top_p": 0.7,
            }
            if self.voice_id:
                req["reference_id"] = self.voice_id
            await self.ws.send(ormsgpack.packb({"event": "start", "request": req}))
            self._reader = asyncio.create_task(self._read())
        except Exception as e:
            self.error = f"tts connect failed: {e}"
            self.done.set()
            raise
        finally:
            self._opened.set()

    async def _read(self) -> None:
        assert self.ws
        try:
            async for frame in self.ws:
                if isinstance(frame, str):
                    continue
                msg = ormsgpack.unpackb(frame)
                ev = msg.get("event")
                if ev == "audio":
                    chunk = self._odd + msg["audio"]
                    # keep 16-bit sample alignment across chunk boundaries
                    if len(chunk) % 2:
                        chunk, self._odd = chunk[:-1], chunk[-1:]
                    else:
                        self._odd = b""
                    if chunk and not self.closed:
                        if self.first_audio_at is None:
                            self.first_audio_at = time.perf_counter()
                        await self.on_audio(chunk)
                elif ev == "finish":
                    if msg.get("reason") == "error":
                        self.error = msg.get("message") or "tts error"
                    break
                elif ev == "error":
                    self.error = str(msg.get("error"))
                    break
        except websockets.ConnectionClosed:
            pass
        except Exception as e:
            self.error = str(e)
        finally:
            self.done.set()

    async def send_text(self, text: str) -> None:
        await self._opened.wait()
        if self.ws is None or self.closed or not text:
            return
        if self.first_text_at is None:
            self.first_text_at = time.perf_counter()
        await self.ws.send(ormsgpack.packb({"event": "text", "text": text}))
        await self.ws.send(ormsgpack.packb({"event": "flush"}))

    async def finish(self) -> None:
        """No more text: let the server drain, then wait for `finish`."""
        await self._opened.wait()
        if self.ws is None or self.closed:
            return
        try:
            await self.ws.send(ormsgpack.packb({"event": "stop"}))
            await asyncio.wait_for(self.done.wait(), 30)
        except Exception as e:
            log.warning("tts finish: %s", e)
        await self.close()

    async def close(self) -> None:
        """Barge-in / cancel: drop everything immediately."""
        self.closed = True
        if self._reader:
            self._reader.cancel()
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass
        self.done.set()


_local_http: httpx.AsyncClient | None = None


def local_http() -> httpx.AsyncClient:
    global _local_http
    if _local_http is None:
        s = get_settings()
        headers = {"Authorization": f"Bearer {s.tts_server_api_key}"} if s.tts_server_api_key else {}
        # Keep-alive so a reply doesn't pay for a new connection; generous read timeout for long sentences.
        _local_http = httpx.AsyncClient(
            base_url=s.tts_server_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(60.0, connect=5.0),
            limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=300),
        )
    return _local_http


class LocalTTSStream:
    """Same interface as `TTSStream`, backed by a self-hosted fish-speech server."""

    WAV_HEADER = 44

    def __init__(self, voice_id: str, on_audio: Callable[[bytes], Awaitable[None]]):
        self.voice_id = voice_id
        self.on_audio = on_audio
        self.first_audio_at: float | None = None
        self.first_text_at: float | None = None
        self.done = asyncio.Event()
        self.error: str | None = None
        self.closed = False
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._worker: asyncio.Task | None = None

    async def open(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            while not self.closed:
                text = await self._queue.get()
                if text is None:
                    break
                await self._speak(text)
                if self.error:
                    break
        except Exception as e:
            self.error = f"tts: {e}"
        finally:
            self.done.set()

    async def _speak(self, text: str) -> None:
        s = get_settings()
        req = {
            "text": text,
            "format": "wav",
            "streaming": True,
            "chunk_length": 100,  # smallest segment the server allows -> earliest first audio
            "normalize": True,
            "use_memory_cache": "on",  # reuse the encoded reference voice across requests
            "temperature": 0.7,
            "top_p": 0.7,
        }
        if self.voice_id:
            req["reference_id"] = self.voice_id
        head = b""
        odd = b""
        async with local_http().stream(
            "POST", "/v1/tts", content=ormsgpack.packb(req), headers={"Content-Type": "application/msgpack"}
        ) as resp:
            if resp.status_code >= 400:
                self.error = f"self-hosted TTS {resp.status_code}: {(await resp.aread())[:300].decode(errors='replace')}"
                return
            async for b in resp.aiter_bytes():
                if self.closed:
                    return
                if len(head) < self.WAV_HEADER:
                    need = self.WAV_HEADER - len(head)
                    head, b = head + b[:need], b[need:]
                    if len(head) == self.WAV_HEADER:
                        rate = int.from_bytes(head[24:28], "little")
                        if rate != s.tts_server_sample_rate:
                            log.error("self-hosted TTS outputs %d Hz but TTS_SERVER_SAMPLE_RATE=%d", rate,
                                      s.tts_server_sample_rate)
                    if not b:
                        continue
                chunk = odd + b
                if len(chunk) % 2:
                    chunk, odd = chunk[:-1], chunk[-1:]
                else:
                    odd = b""
                if chunk:
                    if self.first_audio_at is None:
                        self.first_audio_at = time.perf_counter()
                    await self.on_audio(chunk)

    async def send_text(self, text: str) -> None:
        if self.closed or not text.strip():
            return
        if self.first_text_at is None:
            self.first_text_at = time.perf_counter()
        await self._queue.put(text)

    async def finish(self) -> None:
        if self.closed:
            return
        await self._queue.put(None)
        try:
            await asyncio.wait_for(self.done.wait(), 120)
        except Exception as e:
            log.warning("tts finish: %s", e)
        await self.close()

    async def close(self) -> None:
        """Barge-in / cancel: drop the in-flight request (closing the stream stops delivery)."""
        self.closed = True
        if self._worker and not self._worker.done():
            self._worker.cancel()
        self.done.set()


def new_stream(voice_id: str, on_audio: Callable[[bytes], Awaitable[None]]) -> "TTSStream | LocalTTSStream":
    if get_settings().tts_self_hosted:
        return LocalTTSStream(voice_id, on_audio)
    return TTSStream(voice_id, on_audio)


async def warm() -> None:
    """Self-hosted: open the keep-alive connection before the first reply needs it."""
    s = get_settings()
    if not s.tts_self_hosted:
        return
    try:
        await local_http().get("/v1/health", timeout=5)
    except Exception as e:
        log.warning("self-hosted TTS at %s not reachable: %s", s.tts_server_url, e)


async def synthesize(text: str, voice_id: str) -> bytes:
    """Whole-utterance synthesis (used for pre-cached backchannels)."""
    buf = bytearray()

    async def collect(b: bytes) -> None:
        buf.extend(b)

    stream = new_stream(voice_id, collect)
    await stream.open()
    await stream.send_text(text)
    await stream.finish()
    if stream.error:
        raise RuntimeError(stream.error)
    return bytes(buf)
