"""Speech-to-text for one VAD-delimited utterance (16 kHz mono WAV from the browser)."""

import asyncio
import io
import logging
import time

import httpx

from ..config import get_settings
from ..llm import client as azure_client

log = logging.getLogger(__name__)

_http: httpx.AsyncClient | None = None


def http() -> httpx.AsyncClient:
    global _http
    if _http is None:
        # Keep-alive pool: avoids a TLS handshake on every utterance.
        _http = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=5.0),
            limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=120),
            http2=False,
        )
    return _http


_whisper = None
_whisper_lock = asyncio.Lock()


def _use_mlx() -> bool:
    """Apple Silicon: mlx-whisper runs on the GPU, ~5x faster than CPU faster-whisper for the same model."""
    try:
        import mlx_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _mlx_repo(model: str) -> str:
    return model if "/" in model else f"mlx-community/whisper-{model}-mlx"


def _local_model():
    """Loaded once. faster-whisper (CTranslate2, int8) off Apple Silicon."""
    global _whisper
    if _whisper is None:
        model = get_settings().local_stt_model
        if _use_mlx():
            import numpy as np
            import mlx_whisper

            # Loading happens on first transcribe; run one on silence so the first answer doesn't pay for it.
            mlx_whisper.transcribe(np.zeros(16000, np.float32), path_or_hf_repo=_mlx_repo(model), language="en")
            _whisper = "mlx"
        else:
            from faster_whisper import WhisperModel

            _whisper = WhisperModel(model, device="auto", compute_type="int8")
    return _whisper


def _pcm16_mono_16k(wav: bytes):
    """The browser sends 16 kHz mono PCM16 WAV; decode it directly (no ffmpeg/PyAV round trip)."""
    import wave

    import numpy as np

    with wave.open(io.BytesIO(wav)) as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"expected 16 kHz mono PCM16, got {w.getframerate()} Hz x{w.getnchannels()}")
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


def _local_transcribe(wav: bytes, prompt: str) -> str:
    model = _local_model()
    if model == "mlx":
        import mlx_whisper

        out = mlx_whisper.transcribe(
            _pcm16_mono_16k(wav),
            path_or_hf_repo=_mlx_repo(get_settings().local_stt_model),
            language="en",
            initial_prompt=prompt[-200:] or None,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        return str(out["text"]).strip()
    segments, _ = model.transcribe(
        _pcm16_mono_16k(wav),
        language="en",
        beam_size=1,  # greedy: the latency win is large, the accuracy cost on clean single-speaker audio is small
        initial_prompt=prompt[-200:] or None,
        condition_on_previous_text=False,
        without_timestamps=True,
        vad_filter=False,  # the browser already cut the utterance with Silero VAD
    )
    return " ".join(seg.text.strip() for seg in segments)


async def transcribe(wav: bytes, *, prompt: str = "") -> tuple[str, float]:
    """Return (text, elapsed_ms)."""
    s = get_settings()
    t0 = time.perf_counter()
    if s.stt_provider == "local":
        async with _whisper_lock:
            text = await asyncio.to_thread(_local_transcribe, wav, prompt)
    elif s.stt_provider == "azure":
        f = io.BytesIO(wav)
        f.name = "utterance.wav"
        r = await azure_client().audio.transcriptions.create(
            model=s.azure_openai_transcribe_deployment,
            file=f,
            language="en",
            prompt=prompt[:800] or None,
        )
        text = r.text
    else:
        if not s.fish_api_key:
            raise RuntimeError("FISH_API_KEY is not set")
        r = await http().post(
            "https://api.fish.audio/v1/asr",
            headers={"Authorization": f"Bearer {s.fish_api_key}", "model": s.fish_asr_model},
            files={"audio": ("utterance.wav", wav, "audio/wav")},
            data={"language": "en", "ignore_timestamps": "true", "tag_audio_events": "false"},
        )
        r.raise_for_status()
        text = r.json().get("text", "")
    return text.strip(), (time.perf_counter() - t0) * 1000


async def warm() -> None:
    """Open the keep-alive connection early so the first utterance doesn't pay for TLS."""
    s = get_settings()
    if s.stt_provider == "local":
        try:
            await asyncio.to_thread(_local_model)
        except Exception as e:
            log.warning("local STT model failed to load: %s", e)
    if s.stt_provider == "fish" and s.fish_api_key:
        try:
            await http().get("https://api.fish.audio/", timeout=5)
        except Exception:
            pass
