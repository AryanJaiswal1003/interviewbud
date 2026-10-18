"""LocalTTSStream against a stand-in for fish-speech's /v1/tts (tools/server/views.py): a msgpack request, and with
streaming=true a 44-byte WAV header with unknown sizes followed by PCM16 per generated segment."""

import asyncio
import io
import struct
import wave

import httpx
import ormsgpack
import pytest

from app.config import get_settings
from app.voice import tts


def wav_chunk_header(rate: int) -> bytes:  # same construction as fish_speech.inference_engine.utils
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
    h = buf.getvalue()
    unknown = struct.pack("<I", 0xFFFFFFFF)
    return h[:4] + unknown + h[8:40] + unknown


class FakeFish:
    def __init__(self, rate=44100, status=200, delay=0.0):
        self.rate, self.status, self.delay = rate, status, delay
        self.requests: list[dict] = []

    def pcm_for(self, text: str) -> bytes:
        # Distinct, recognisable samples per request so ordering is checkable.
        return struct.pack("<h", len(self.requests)) * (len(text) * 3)

    async def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/tts"
        assert request.headers["content-type"] == "application/msgpack"
        body = ormsgpack.unpackb(request.content)
        self.requests.append(body)
        if self.status >= 400:
            return httpx.Response(self.status, text="Failed to generate speech")
        pcm = self.pcm_for(body["text"])
        delay = self.delay

        async def stream():
            data = wav_chunk_header(self.rate) + pcm
            # Odd-sized pieces: the header and samples split across reads.
            for i in range(0, len(data), 7):
                if delay:
                    await asyncio.sleep(delay)
                yield data[i:i + 7]

        return httpx.Response(200, content=stream(), headers={"content-type": "audio/wav"})


@pytest.fixture
def fish(monkeypatch):
    def make(**kw):
        fake = FakeFish(**kw)
        client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler), base_url="http://fish")
        monkeypatch.setattr(tts, "_local_http", client)
        monkeypatch.setattr(get_settings(), "tts_provider", "fish_local")
        return fake
    return make


async def test_streams_pcm_in_order_without_wav_header(fish):
    fake = fish()
    got = bytearray()
    sizes = []

    async def on_audio(b: bytes):
        sizes.append(len(b))
        got.extend(b)

    s = tts.new_stream("interviewer-voice", on_audio)
    assert isinstance(s, tts.LocalTTSStream)
    await s.open()
    await s.send_text("Thanks, that's helpful. ")
    await s.send_text("What did you measure? ")
    await s.send_text("   ")  # whitespace-only pieces are not sent
    await s.finish()

    assert s.error is None
    assert [r["text"] for r in fake.requests] == ["Thanks, that's helpful. ", "What did you measure? "]
    r0 = fake.requests[0]
    assert r0["reference_id"] == "interviewer-voice" and r0["streaming"] is True and r0["format"] == "wav"
    assert r0["chunk_length"] == 100 and r0["use_memory_cache"] == "on"
    first = struct.pack("<h", 1) * (len("Thanks, that's helpful. ") * 3)
    second = struct.pack("<h", 2) * (len("What did you measure? ") * 3)
    assert bytes(got) == first + second  # header stripped, nothing lost, requests in order
    assert all(n % 2 == 0 for n in sizes)  # every delivery is whole 16-bit samples
    assert s.first_audio_at is not None and s.first_text_at is not None


async def test_server_error_is_reported(fish):
    fish(status=500)
    s = tts.new_stream("", lambda b: asyncio.sleep(0))
    await s.open()
    await s.send_text("Hello there.")
    await s.finish()
    assert s.error and "500" in s.error


async def test_no_voice_means_no_reference_id(fish):
    fake = fish()
    pcm = await tts.synthesize("Hi.", "")
    assert "reference_id" not in fake.requests[0]
    assert len(pcm) == len("Hi.") * 3 * 2


async def test_close_stops_delivery_midway(fish):
    fish(delay=0.01)
    got = bytearray()

    async def on_audio(b: bytes):
        got.extend(b)

    s = tts.new_stream("v", on_audio)
    await s.open()
    await s.send_text("A fairly long sentence that takes a while to stream back. ")
    await asyncio.sleep(0.05)
    await s.close()  # barge-in
    n = len(got)
    await asyncio.sleep(0.1)
    assert len(got) == n and n < len("A fairly long sentence that takes a while to stream back. ") * 6
    assert s.done.is_set()
