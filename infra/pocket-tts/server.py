"""Pocket TTS (Kyutai, github.com/kyutai-labs/pocket-tts) server for InterviewBud. Runs on CPU.

Speaks the subset of fish-speech's HTTP API (tools/server/views.py) that InterviewBud's self-hosted client uses:

  POST   /v1/tts               msgpack or JSON {text, reference_id?, format: wav|pcm, streaming}
                               streaming=true -> 44-byte WAV header (sizes 0xFFFFFFFF), then PCM16 mono 24 kHz in
                               ~80 ms pieces as they are generated. Closing the connection stops generation.
  POST   /v1/references/add    multipart: id, audio, text (text accepted for compatibility, unused)
  GET    /v1/references/list   built-in catalog voices + added ones
  DELETE /v1/references/delete form: reference_id
  GET    /v1/health

Voice cloning (references/add) needs the cloning weights, which Kyutai gates behind accepting terms at
huggingface.co/kyutai/pocket-tts; the catalog voices work without them.
"""

import asyncio
import io
import logging
import os
import re
import struct
import threading
import time
import wave
from pathlib import Path

import numpy as np
import ormsgpack
import torch
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pocket_tts import TTSModel
from pocket_tts.models.model_state import export_model_state

log = logging.getLogger("pocket-tts-server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

API_KEY = os.getenv("API_KEY", "")
REFS = Path(os.getenv("REFERENCES_DIR", Path(__file__).parent / "references"))
DEFAULT_VOICE = os.getenv("DEFAULT_VOICE", "alba")
LANGUAGE = os.getenv("LANGUAGE") or None
ID_RE = re.compile(r"^[A-Za-z0-9_\- ]{1,255}$")
TAG_RE = re.compile(r"(?<![\w\])])\[[a-z][a-z \-']{1,30}\]", re.I)  # Pocket TTS has no voice cues; never read them out
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

app = FastAPI(title="InterviewBud Pocket TTS")
model: TTSModel | None = None
catalog: list[str] = []
states: dict[str, dict] = {}
cpu = threading.Lock()  # the model is not thread-safe: one generation at a time


def auth(request: Request) -> None:
    if API_KEY and request.headers.get("authorization", "") != f"Bearer {API_KEY}":
        raise HTTPException(401, "Invalid token")


def clean(text: str) -> str:
    return re.sub(r"\s{2,}", " ", TAG_RE.sub("", text)).strip()


def wav_stream_header(rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
    h = buf.getvalue()
    unknown = struct.pack("<I", 0xFFFFFFFF)
    return h[:4] + unknown + h[8:40] + unknown


def pcm16(a: torch.Tensor) -> bytes:
    return (np.clip(a.numpy(), -1.0, 1.0 - 1 / 32768) * 32768).astype("<i2").tobytes()


def state_for(ref_id: str | None) -> dict:
    ref_id = ref_id or DEFAULT_VOICE
    if ref_id in states:
        return states[ref_id]
    d = REFS / ref_id
    if (d / "voice.safetensors").exists():
        src: str | Path = d / "voice.safetensors"
    elif ref_id in catalog:
        src = ref_id
    else:
        raise HTTPException(404, f"Unknown reference_id '{ref_id}'")
    with cpu:
        states[ref_id] = model.get_state_for_audio_prompt(src)
    return states[ref_id]


def generate(text: str, ref_id: str | None, stop: threading.Event, emit) -> None:
    st = state_for(ref_id)
    t0, first, samples = time.perf_counter(), None, 0
    with cpu:
        for chunk in model.generate_audio_stream(st, text, stop=stop):
            if first is None:
                first = time.perf_counter() - t0
            samples += chunk.shape[-1]
            emit(pcm16(chunk))
    el = time.perf_counter() - t0
    dur = samples / model.sample_rate
    log.info("tts first audio %.0f ms, %.2fs for %.2fs audio (%.1fx realtime)%s voice=%s: %r",
             (first or 0) * 1000, el, dur, dur / max(el, 1e-6), " STOPPED" if stop.is_set() else "",
             ref_id or DEFAULT_VOICE, text[:80])


@app.on_event("startup")
def load() -> None:
    global model, catalog
    REFS.mkdir(parents=True, exist_ok=True)
    t = time.perf_counter()
    model = TTSModel.load_model(language=LANGUAGE)
    from pocket_tts.utils.utils import _ORIGINS_OF_PREDEFINED_VOICES  # the released catalog voices
    catalog = sorted(_ORIGINS_OF_PREDEFINED_VOICES)
    st = state_for(DEFAULT_VOICE)
    with cpu:
        list(model.generate_audio_stream(st, "Warming up."))
    log.info("Pocket TTS ready in %.1fs, %d threads, voices: %s", time.perf_counter() - t, torch.get_num_threads(),
             ", ".join(catalog))


@app.get("/v1/health")
async def health():
    return {"status": "ok" if model is not None else "loading", "model": "pocket-tts",
            "sample_rate": model.sample_rate if model else None, "default_voice": DEFAULT_VOICE}


@app.post("/v1/tts", dependencies=[Depends(auth)])
async def tts(request: Request):
    raw = await request.body()
    body = ormsgpack.unpackb(raw) if "msgpack" in request.headers.get("content-type", "") else await request.json()
    text = clean(str(body.get("text", "")))
    if not text:
        raise HTTPException(400, "text is empty")
    ref_id = body.get("reference_id") or None
    fmt = body.get("format", "wav")
    if fmt not in ("wav", "pcm"):
        raise HTTPException(400, "format must be wav or pcm")
    if body.get("streaming") and fmt != "wav":
        raise HTTPException(400, "Streaming only supports WAV format")
    await asyncio.to_thread(state_for, ref_id)  # 404 before streaming starts
    parts = [p for p in SENTENCE_RE.split(text) if p.strip()] if len(text) > 300 else [text]
    loop = asyncio.get_running_loop()
    stop = threading.Event()

    def work(q: asyncio.Queue) -> None:
        try:
            for p in parts:
                if stop.is_set():
                    break
                generate(p, ref_id, stop, lambda b: loop.call_soon_threadsafe(q.put_nowait, b))
            loop.call_soon_threadsafe(q.put_nowait, None)
        except BaseException as e:
            log.exception("tts failed")
            loop.call_soon_threadsafe(q.put_nowait, e)

    if body.get("streaming"):
        async def gen():
            q: asyncio.Queue = asyncio.Queue()
            task = loop.run_in_executor(None, work, q)
            try:
                yield wav_stream_header(model.sample_rate)
                while (item := await q.get()) is not None:
                    if isinstance(item, BaseException):
                        break
                    yield item
            finally:
                stop.set()  # client went away (barge-in) or we finished: stop generating either way
                await task
        return StreamingResponse(gen(), media_type="audio/wav")

    q: asyncio.Queue = asyncio.Queue()
    await loop.run_in_executor(None, work, q)
    pcm = bytearray()
    while (item := q.get_nowait()) is not None:
        if isinstance(item, BaseException):
            raise HTTPException(500, "Failed to generate speech")
        pcm.extend(item)
    if fmt == "pcm":
        return Response(bytes(pcm), media_type="audio/pcm")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(model.sample_rate)
        w.writeframes(bytes(pcm))
    return Response(buf.getvalue(), media_type="audio/wav")


@app.post("/v1/references/add", dependencies=[Depends(auth)])
async def add_reference(id: str = Form(...), audio: UploadFile = File(...), text: str = Form("")):
    if not ID_RE.match(id):
        raise HTTPException(400, "Reference ID may use letters, digits, hyphens, underscores and spaces")
    if id in catalog or (REFS / id).exists():
        return JSONResponse({"success": False, "message": f"Reference ID '{id}' already exists",
                             "reference_id": id}, status_code=409)
    data = await audio.read()
    if not data:
        raise HTTPException(400, "Audio file is empty")
    d = REFS / id
    d.mkdir(parents=True)
    (d / "sample.wav").write_bytes(data)

    def build():
        with cpu:
            st = model.get_state_for_audio_prompt(d / "sample.wav", truncate=True)
        export_model_state(st, d / "voice.safetensors")  # reloads in milliseconds next time
        states[id] = st

    try:
        await asyncio.to_thread(build)
    except Exception as e:
        for f in d.iterdir():
            f.unlink()
        d.rmdir()
        msg = str(e)
        if "voice cloning" in msg:
            raise HTTPException(403, "Voice cloning needs Kyutai's gated weights: accept the terms at "
                                     "https://huggingface.co/kyutai/pocket-tts, then `uvx hf auth login` on this "
                                     "machine and restart the TTS server.")
        raise HTTPException(400, f"Could not use that audio as a voice reference: {msg[:300]}")
    return {"success": True, "message": f"Reference voice '{id}' added successfully", "reference_id": id}


@app.get("/v1/references/list", dependencies=[Depends(auth)])
async def list_references():
    added = sorted(p.name for p in REFS.iterdir() if (p / "voice.safetensors").exists())
    return {"success": True, "reference_ids": added + catalog, "message": "Success"}


@app.delete("/v1/references/delete", dependencies=[Depends(auth)])
async def delete_reference(reference_id: str = Form(...)):
    d = REFS / reference_id
    if not ID_RE.match(reference_id) or not d.is_dir():
        raise HTTPException(404, f"Unknown reference_id '{reference_id}'")
    for f in d.iterdir():
        f.unlink()
    d.rmdir()
    states.pop(reference_id, None)
    return {"success": True, "message": f"Reference voice '{reference_id}' deleted", "reference_id": reference_id}
