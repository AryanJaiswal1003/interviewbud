"""Chatterbox TTS server for InterviewBud.

Speaks the subset of fish-speech's HTTP API (tools/server/views.py) that InterviewBud uses, so the app's
self-hosted TTS client works against either server:

  POST   /v1/tts               msgpack or JSON {text, reference_id?, format: wav|pcm, streaming, temperature, top_p}
                               streaming=true -> 44-byte WAV header (sizes 0xFFFFFFFF) then PCM16 mono as it is
                               generated (first piece after ~0.6 s of speech tokens, not after the whole sentence)
  POST   /v1/references/add    multipart: id, audio, text (text is accepted for compatibility; Chatterbox ignores it)
  GET    /v1/references/list
  DELETE /v1/references/delete form: reference_id
  GET    /v1/health

Model: Chatterbox-Turbo (default) or Chatterbox-Nano, github.com/resemble-ai/chatterbox (MIT). Every clip carries
Resemble's imperceptible Perth watermark.
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
import torch.nn.functional as F
from chatterbox.models.s3gen.const import S3GEN_SIL
from chatterbox.tts_turbo import ChatterboxTurboTTS, Conditionals, punc_norm
from transformers.generation.logits_process import (
    LogitsProcessorList,
    RepetitionPenaltyLogitsProcessor,
    TemperatureLogitsWarper,
    TopKLogitsWarper,
    TopPLogitsWarper,
)
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

log = logging.getLogger("chatterbox-server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

MODEL = os.getenv("CHATTERBOX_MODEL", "turbo")  # turbo | nano
DEVICE = os.getenv("DEVICE", "cuda" if torch.cuda.is_available() else "cpu")
API_KEY = os.getenv("API_KEY", "")
REFS = Path(os.getenv("REFERENCES_DIR", "/data/references"))
# bf16 halves the token generator's memory traffic: 21.6 -> 12.7 ms/token on an A10 slice, same words out.
T3_BF16 = os.getenv("T3_BF16", "1") == "1" and torch.cuda.is_available() and torch.cuda.is_bf16_supported()
# Streaming: decode once this many new speech tokens (25/s) exist; the first piece is smaller so speech starts sooner.
FIRST_CHUNK = int(os.getenv("FIRST_CHUNK_TOKENS", "16"))
NEXT_CHUNK = int(os.getenv("NEXT_CHUNK_TOKENS", "28"))
MEL_CACHE = 8  # mel frames held back each piece and crossfaded into the next (CosyVoice-style)
HOP = 480  # samples per mel frame at 24 kHz
ID_RE = re.compile(r"^[A-Za-z0-9_\- ]{1,255}$")

# Turbo's tokenizer has these paralinguistic tokens; any other [bracket] cue would be read out as words.
SUPPORTED_TAGS = {
    "advertisement", "angry", "chuckle", "clear throat", "cough", "crying", "dramatic", "fear", "gasp", "groan",
    "happy", "laugh", "narration", "sarcastic", "shush", "sigh", "sniff", "surprised", "whispering",
}
TAG_RE = re.compile(r"(?<![\w\])])\[([a-z][a-z \-']{1,30})\]", re.I)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

app = FastAPI(title="InterviewBud Chatterbox TTS")
model: ChatterboxTurboTTS | None = None
default_conds: Conditionals | None = None
voices: dict[str, Conditionals] = {}
gpu = threading.Lock()  # one generation at a time on the GPU


def auth(request: Request) -> None:
    if API_KEY and request.headers.get("authorization", "") != f"Bearer {API_KEY}":
        raise HTTPException(401, "Invalid token")


def clean(text: str) -> str:
    def keep(m: re.Match) -> str:
        tag = m.group(1).lower().strip()
        return f"[{tag}]" if tag in SUPPORTED_TAGS else ""
    return re.sub(r"\s{2,}", " ", TAG_RE.sub(keep, text)).strip()


def wav_stream_header(rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
    h = buf.getvalue()
    unknown = struct.pack("<I", 0xFFFFFFFF)
    return h[:4] + unknown + h[8:40] + unknown


def pcm16(wav: torch.Tensor) -> bytes:
    a = wav.squeeze().detach().cpu().numpy()
    return (np.clip(a, -1.0, 1.0 - 1 / 32768) * 32768).astype("<i2").tobytes()


def t3_ready(c: Conditionals) -> Conditionals:
    if T3_BF16:
        for k, v in vars(c.t3).items():
            if torch.is_tensor(v) and v.is_floating_point():
                setattr(c.t3, k, v.to(torch.bfloat16))
    return c


def conds_for(ref_id: str | None) -> Conditionals:
    if not ref_id:
        return default_conds
    if ref_id in voices:
        return voices[ref_id]
    d = REFS / ref_id
    cached = d / "conds.pt"
    if cached.exists():
        c = t3_ready(Conditionals.load(cached, map_location=DEVICE).to(DEVICE))
    else:
        samples = sorted(p for p in d.glob("sample.*") if p.suffix != ".pt")
        if not samples:
            raise HTTPException(404, f"Unknown reference_id '{ref_id}'")
        with gpu:
            model.prepare_conditionals(str(samples[0]))
            c = model.conds
            model.conds = default_conds
        c.save(cached)
        c = t3_ready(c)
    voices[ref_id] = c
    return c


def synth(text: str, ref_id: str | None, temperature: float, top_p: float) -> bytes:
    out = bytearray()
    synth_stream(text, ref_id, temperature, top_p, out.extend)
    return bytes(out)


def speech_tokens(c: Conditionals, text: str, temperature: float, top_p: float, max_len: int = 1000):
    """ChatterboxTurboTTS.t3.inference_turbo as a generator, so audio can be decoded while tokens are still coming."""
    t3 = model.t3
    procs = LogitsProcessorList([TemperatureLogitsWarper(temperature), TopKLogitsWarper(1000),
                                 TopPLogitsWarper(top_p), RepetitionPenaltyLogitsProcessor(1.2)])
    text_tokens = model.tokenizer(punc_norm(text), return_tensors="pt").input_ids.to(DEVICE)
    start = t3.hp.start_speech_token * torch.ones_like(text_tokens[:, :1])
    embeds, _ = t3.prepare_input_embeds(t3_cond=c.t3, text_tokens=text_tokens, speech_tokens=start, cfg_weight=0.0)
    out = t3.tfmr(inputs_embeds=embeds, use_cache=True)
    past, logits = out.past_key_values, t3.speech_head(out[0][:, -1:])
    history = start
    for _ in range(max_len):
        probs = F.softmax(procs(history, logits[:, -1, :]).float(), dim=-1)
        nxt = torch.multinomial(probs, num_samples=1)
        tok = int(nxt)
        if tok == t3.hp.stop_speech_token:
            return
        history = torch.cat([history, nxt], dim=1)
        if tok < 6561:  # drop out-of-vocabulary tokens, as generate() does
            yield tok
        out = t3.tfmr(inputs_embeds=t3.speech_emb(nxt), past_key_values=past, use_cache=True)
        past, logits = out.past_key_values, t3.speech_head(out[0])


def synth_stream(text: str, ref_id: str | None, temperature: float, top_p: float, emit) -> None:
    """Generate tokens on one thread while this one decodes every new chunk to audio and emits it."""
    c = conds_for(ref_id)
    tokens: list[int] = []
    done = threading.Event()
    failed: list[BaseException] = []
    cv = threading.Condition()

    def produce():
        try:
            with torch.inference_mode():
                for tok in speech_tokens(c, text, temperature, top_p):
                    with cv:
                        tokens.append(tok)
                        cv.notify()
        except BaseException as e:
            failed.append(e)
        finally:
            done.set()
            with cv:
                cv.notify()

    t0 = time.perf_counter()
    with gpu, torch.inference_mode():
        producer = threading.Thread(target=produce, daemon=True)
        producer.start()
        s3 = model.s3gen
        ref = {k: (v.to(DEVICE) if torch.is_tensor(v) else v) for k, v in c.gen.items()}
        noise = torch.randn(1, 80, 2 * 1010, device=DEVICE, dtype=s3.dtype)  # fixed so pieces agree
        window = torch.from_numpy(np.hamming(2 * MEL_CACHE * HOP)).to(DEVICE, torch.float32)
        emitted_mel, cache_mel, cache_src, cache_wav = 0, None, None, None
        decoded, first_at, pieces, samples = 0, None, 0, 0
        while True:
            want = FIRST_CHUNK if decoded == 0 else NEXT_CHUNK
            with cv:
                cv.wait_for(lambda: done.is_set() or len(tokens) - decoded >= want)
                n, finalize = len(tokens), done.is_set()
            if failed:
                raise failed[0]
            if n == 0:
                break
            toks = torch.tensor(tokens[:n] + ([S3GEN_SIL] * 3 if finalize else []), device=DEVICE)
            mel_len = 2 * toks.numel() - (0 if finalize else 6)  # without finalize the last 3 tokens are lookahead
            mel = s3(speech_tokens=toks, ref_wav=None, ref_sr=None, ref_dict=ref, finalize=finalize,
                     n_cfm_timesteps=2, noised_mels=noise[:, :, :mel_len], skip_vocoder=True)
            new = mel[:, :, emitted_mel:]
            emitted_mel = mel.shape[2]
            if cache_mel is not None:
                new = torch.cat([cache_mel, new], dim=2)
            wav, src = s3.mel2wav.inference(speech_feat=new.to(s3.dtype),
                                            cache_source=cache_src if cache_src is not None
                                            else torch.zeros(1, 1, 0, device=DEVICE))
            wav = wav.float()
            if cache_wav is not None:
                k = MEL_CACHE * HOP
                wav[:, :k] = wav[:, :k] * window[:k] + cache_wav * window[k:]
            else:
                wav[:, :len(s3.trim_fade)] *= s3.trim_fade.to(wav.device)  # s3gen's fade against reference spillover
            if not finalize:
                k = MEL_CACHE * HOP
                cache_mel, cache_src, cache_wav = new[:, :, -MEL_CACHE:], src[:, :, -k:], wav[:, -k:].clone()
                wav = wav[:, :-k]
            decoded = n
            if wav.numel():
                a = wav.squeeze(0).cpu().numpy()
                a = model.watermarker.apply_watermark(a, sample_rate=model.sr)
                emit((np.clip(a, -1.0, 1.0 - 1 / 32768) * 32768).astype("<i2").tobytes())
                pieces += 1
                samples += a.shape[-1]
                if first_at is None:
                    first_at = time.perf_counter() - t0
            if finalize:
                break
        producer.join()
    el = time.perf_counter() - t0
    dur = samples / model.sr
    log.info("tts stream first audio %.2fs, %.2fs for %.2fs audio (RTF %.2f), %d pieces, voice=%s: %r",
             first_at or -1, el, dur, el / max(dur, 1e-6), pieces, ref_id or "default", text[:80])


@app.on_event("startup")
def load() -> None:
    global model, default_conds
    REFS.mkdir(parents=True, exist_ok=True)
    t = time.perf_counter()
    model = ChatterboxTurboTTS.from_pretrained(device=DEVICE, nano=MODEL == "nano")
    if T3_BF16:
        model.t3.to(torch.bfloat16)
    default_conds = t3_ready(model.conds)
    for _ in range(2):  # CUDA kernels, allocator
        synth_stream("Warming up the voice.", None, 0.8, 0.95, lambda b: None)
    log.info("Chatterbox-%s ready on %s in %.1fs", MODEL, DEVICE, time.perf_counter() - t)


@app.get("/v1/health")
async def health():
    return {"status": "ok" if model is not None else "loading", "model": f"chatterbox-{MODEL}", "device": DEVICE,
            "sample_rate": model.sr if model else None}


@app.post("/v1/tts", dependencies=[Depends(auth)])
async def tts(request: Request):
    raw = await request.body()
    ctype = request.headers.get("content-type", "")
    body = ormsgpack.unpackb(raw) if "msgpack" in ctype else await request.json()
    text = clean(str(body.get("text", "")))
    if not text:
        raise HTTPException(400, "text is empty")
    ref_id = body.get("reference_id") or None
    temperature = float(body.get("temperature") or 0.8)
    top_p = float(body.get("top_p") or 0.95)
    fmt = body.get("format", "wav")
    streaming = bool(body.get("streaming"))
    if fmt not in ("wav", "pcm"):
        raise HTTPException(400, "format must be wav or pcm")
    if streaming and fmt != "wav":
        raise HTTPException(400, "Streaming only supports WAV format")
    # Long input: one generation per sentence so audio starts after the first one.
    parts = [p for p in SENTENCE_RE.split(text) if p.strip()] if len(text) > 220 else [text]

    if streaming:
        loop = asyncio.get_running_loop()
        q: asyncio.Queue[bytes | None | BaseException] = asyncio.Queue()

        def work():
            try:
                for p in parts:
                    synth_stream(p, ref_id, temperature, top_p, lambda b: loop.call_soon_threadsafe(q.put_nowait, b))
                loop.call_soon_threadsafe(q.put_nowait, None)
            except BaseException as e:
                log.exception("tts failed")
                loop.call_soon_threadsafe(q.put_nowait, e)

        async def gen():
            yield wav_stream_header(model.sr)
            task = loop.run_in_executor(None, work)
            while (item := await q.get()) is not None:
                if isinstance(item, BaseException):
                    break  # the client sees a short stream; details are in the server log
                yield item
            await task
        return StreamingResponse(gen(), media_type="audio/wav")

    pcm = b"".join([await asyncio.to_thread(synth, p, ref_id, temperature, top_p) for p in parts])
    if fmt == "pcm":
        return Response(pcm, media_type="audio/pcm")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(model.sr)
        w.writeframes(pcm)
    return Response(buf.getvalue(), media_type="audio/wav")


@app.post("/v1/references/add", dependencies=[Depends(auth)])
async def add_reference(id: str = Form(...), audio: UploadFile = File(...), text: str = Form("")):
    if not ID_RE.match(id):
        raise HTTPException(400, "Reference ID may use letters, digits, hyphens, underscores and spaces")
    d = REFS / id
    if d.exists():
        return JSONResponse({"success": False, "message": f"Reference ID '{id}' already exists",
                             "reference_id": id}, status_code=409)
    data = await audio.read()
    if not data:
        raise HTTPException(400, "Audio file is empty")
    d.mkdir(parents=True)
    (d / "sample.wav").write_bytes(data)
    if text.strip():
        (d / "sample.lab").write_text(text.strip())
    try:
        await asyncio.to_thread(conds_for, id)  # validate the clip and cache its voice embedding now
    except Exception as e:
        for f in d.iterdir():
            f.unlink()
        d.rmdir()
        raise HTTPException(400, f"Could not use that audio as a voice reference: {e}")
    return {"success": True, "message": f"Reference voice '{id}' added successfully", "reference_id": id}


@app.get("/v1/references/list", dependencies=[Depends(auth)])
async def list_references():
    ids = sorted(p.name for p in REFS.iterdir() if p.is_dir() and any(p.glob("sample.*")))
    return {"success": True, "reference_ids": ids, "message": "Success"}


@app.delete("/v1/references/delete", dependencies=[Depends(auth)])
async def delete_reference(reference_id: str = Form(...)):
    d = REFS / reference_id
    if not ID_RE.match(reference_id) or not d.is_dir():
        raise HTTPException(404, f"Unknown reference_id '{reference_id}'")
    for f in d.iterdir():
        f.unlink()
    d.rmdir()
    voices.pop(reference_id, None)
    return {"success": True, "message": f"Reference voice '{reference_id}' deleted", "reference_id": reference_id}
