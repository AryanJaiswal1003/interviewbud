"""Problems, voices, learning, health."""

import asyncio
import re
import shutil
import uuid

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import auth
from ..agent.planner import budgets_for
from ..coding import sandbox
from ..config import get_settings
from ..db import PlaybookLesson, Problem, ProblemTest, Voice, get_db, utcnow

router = APIRouter(prefix="/api", tags=["catalog"], dependencies=[Depends(auth.current_user)])


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    s = get_settings()
    problems = await db.scalar(select(func.count()).select_from(Problem))
    try:
        await sandbox.ensure_container()
        sb = True
    except Exception as e:
        sb = str(e)
    return {
        "db": True,
        "problems": problems,
        "azure_openai": bool(s.azure_openai_endpoint and s.azure_openai_api_key),
        "chat_deployment": s.azure_openai_chat_deployment,
        "reasoning_deployment": s.reasoning_deployment,
        "fish_audio": bool(s.fish_api_key),
        "tts_provider": s.tts_provider,
        "tts_ready": await _tts_ready(),
        "tts_model": {"fish_local": "fish-speech (self-hosted)", "chatterbox": "Chatterbox (self-hosted)",
                       "pocket": "Pocket TTS (self-hosted)"}.get(
            s.tts_provider, s.fish_tts_model),
        "stt_provider": s.stt_provider,
        "default_voice": s.fish_voice_id or None,
        "sandbox": sb,
        "phase_budgets": {d: budgets_for(d) for d in (30, 45, 60)},
    }


# ---------- problems ----------
@router.get("/problems")
async def list_problems(q: str = "", difficulty: str = "", tag: str = "", limit: int = 50, offset: int = 0,
                        db: AsyncSession = Depends(get_db)):
    stmt = select(Problem.id, Problem.title, Problem.difficulty, Problem.tags, Problem.elo, Problem.attempts,
                  Problem.estimated_date, Problem.split)
    if q:
        stmt = stmt.where(or_(Problem.title.ilike(f"%{q}%"), Problem.slug.ilike(f"%{q}%")))
    if difficulty:
        stmt = stmt.where(Problem.difficulty == difficulty)
    if tag:
        stmt = stmt.where(Problem.tags.contains([tag]))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (await db.execute(stmt.order_by(Problem.attempts.desc(), Problem.id).limit(min(limit, 200))
                             .offset(offset))).all()
    return {"total": total, "items": [dict(r._mapping) for r in rows]}


@router.get("/problems/{pid}")
async def get_problem(pid: int, db: AsyncSession = Depends(get_db)):
    p = await db.get(Problem, pid)
    if not p:
        raise HTTPException(404)
    counts = dict((await db.execute(
        select(ProblemTest.kind, func.count()).where(ProblemTest.problem_id == pid,
                                                     ProblemTest.interview_id.is_(None))
        .group_by(ProblemTest.kind))).all())
    return {"id": p.id, "slug": p.slug, "title": p.title, "difficulty": p.difficulty, "tags": p.tags,
            "description": p.description, "constraints": p.constraints, "examples": p.examples,
            "starter_code": p.starter_code, "elo": p.elo, "attempts": p.attempts, "test_counts": counts}


async def _tts_ready() -> bool | str:
    s = get_settings()
    if not s.tts_self_hosted:
        return bool(s.fish_api_key)
    from ..voice.tts import local_http
    try:
        r = await local_http().get("/v1/health", timeout=3)
        return r.status_code == 200 or f"{s.tts_server_url} answered {r.status_code}"
    except Exception as e:
        return f"{s.tts_server_url} unreachable ({type(e).__name__})"


# ---------- voices (Fish Audio cloud, or a self-hosted fish-speech reference store) ----------
def _local() -> bool:
    return get_settings().tts_self_hosted


async def _to_wav(audio: bytes) -> bytes:
    """The self-hosted server stores uploads under a .wav name, so hand it real WAV (44.1 kHz mono)."""
    if audio[:4] == b"RIFF" or not shutil.which("ffmpeg"):
        return audio
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", "44100", "-f", "wav",
        "pipe:1", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate(audio)
    if proc.returncode:
        raise HTTPException(400, f"could not read the audio sample: {err.decode()[:300]}")
    return out


def _fish_headers() -> dict:
    s = get_settings()
    if not s.fish_api_key:
        raise HTTPException(400, "FISH_API_KEY is not configured")
    return {"Authorization": f"Bearer {s.fish_api_key}"}


@router.get("/voices")
async def list_voices(db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(Voice).order_by(Voice.created_at.desc()))).all()
    return [{"id": str(v.id), "name": v.name, "fish_model_id": v.fish_model_id, "description": v.description,
             "is_default": v.is_default} for v in rows]


@router.get("/voices/fish")
async def fish_voices(mine: bool = True, title: str = ""):
    if _local():
        from ..voice.tts import local_http
        r = await local_http().get("/v1/references/list", headers={"Accept": "application/json"})
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text[:400])
        ids = [i for i in r.json().get("reference_ids", []) if title.lower() in i.lower()]
        return [{"fish_model_id": i, "title": i, "description": "reference voice on your TTS server",
                 "state": "trained", "languages": None, "cover": None} for i in ids]
    params = {"page_size": 30, "page_number": 1, "self": str(mine).lower()}
    if title:
        params["title"] = title
    if not mine:
        params["sort_by"] = "score"
        params["language"] = "en"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get("https://api.fish.audio/model", params=params, headers=_fish_headers())
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text[:400])
    data = r.json()
    return [{"fish_model_id": m["_id"], "title": m.get("title"), "description": m.get("description", ""),
             "state": m.get("state"), "languages": m.get("languages"), "cover": m.get("cover_image")}
            for m in data.get("items", [])]


class VoiceIn(BaseModel):
    name: str
    fish_model_id: str
    description: str = ""


@router.post("/voices")
async def add_voice(body: VoiceIn, db: AsyncSession = Depends(get_db),
                 admin=Depends(auth.require_admin)):
    v = Voice(name=body.name, fish_model_id=body.fish_model_id, description=body.description)
    if not await db.scalar(select(func.count()).select_from(Voice)):
        v.is_default = True
    db.add(v)
    await db.commit()
    return {"id": str(v.id)}


@router.post("/voices/clone")
async def clone_voice(
    name: str = Form(...),
    transcript: str = Form(""),
    sample: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    admin=Depends(auth.require_admin),
):
    """Create a Fish Audio voice model from a 10-30s sample (POST /model, train_mode=fast), or, when self-hosted,
    store it as a reference voice (POST /v1/references/add), which needs the sample's transcript."""
    audio = await sample.read()
    if len(audio) > 20 * 1024 * 1024:
        raise HTTPException(413, "sample too large")
    if _local():
        return await _clone_local(name, transcript, audio, db)
    data = {"type": "tts", "title": name, "train_mode": "fast", "visibility": "private",
            "enhance_audio_quality": "true", "description": "InterviewBud interviewer voice"}
    if transcript.strip():
        data["texts"] = transcript.strip()
    files = {"voices": (sample.filename or "sample.wav", audio, sample.content_type or "audio/wav")}
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post("https://api.fish.audio/model", data=data, files=files, headers=_fish_headers())
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text[:600])
    model = r.json()
    v = Voice(name=name, fish_model_id=model["_id"], description=f"cloned ({model.get('state')})")
    if not await db.scalar(select(func.count()).select_from(Voice)):
        v.is_default = True
    db.add(v)
    await db.commit()
    return {"id": str(v.id), "fish_model_id": v.fish_model_id, "state": model.get("state")}


async def _clone_local(name: str, transcript: str, audio: bytes, db: AsyncSession) -> dict:
    from ..voice import stt
    from ..voice.tts import local_http
    wav = await _to_wav(audio)
    text = transcript.strip()
    if not text:
        try:
            text, _ = await stt.transcribe(wav)
        except Exception as e:
            raise HTTPException(400, f"Add the sample's transcript (automatic transcription failed: {e})")
    # Reference ids allow letters, digits, hyphens, underscores and spaces.
    ref_id = (re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")[:40] or "voice") + "-" + uuid.uuid4().hex[:6]
    r = await local_http().post("/v1/references/add", data={"id": ref_id, "text": text},
                                files={"audio": ("sample.wav", wav, "audio/wav")},
                                headers={"Accept": "application/json"})
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text[:600])
    v = Voice(name=name, fish_model_id=ref_id, description="self-hosted reference voice")
    if not await db.scalar(select(func.count()).select_from(Voice)):
        v.is_default = True
    db.add(v)
    await db.commit()
    return {"id": str(v.id), "fish_model_id": ref_id, "state": "ready on your server"}


@router.post("/voices/{voice_id}/default")
async def set_default(voice_id: str, db: AsyncSession = Depends(get_db),
                 admin=Depends(auth.require_admin)):
    await db.execute(update(Voice).values(is_default=False))
    await db.execute(update(Voice).where(Voice.id == voice_id).values(is_default=True))
    await db.commit()
    return {"ok": True}


@router.post("/voices/preview")
async def preview_voice(body: dict):
    from fastapi.responses import Response

    from ..voice.tts import synthesize
    text = str(body.get("text") or "Hi, I'm your interviewer today. Ready when you are.")[:300]
    pcm = await synthesize(text, str(body.get("fish_model_id") or ""))
    return Response(pcm, media_type="application/octet-stream",
                    headers={"X-Sample-Rate": str(get_settings().tts_output_rate)})


# ---------- learning ----------
@router.get("/learning/lessons")
async def lessons(db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(PlaybookLesson).order_by(PlaybookLesson.status, PlaybookLesson.updated_at.desc())
                             )).all()
    return [{"id": l.id, "scope": l.scope, "lesson": l.lesson, "evidence": l.evidence, "status": l.status,
             "helpful": l.helpful, "harmful": l.harmful, "source_interview_id": l.source_interview_id,
             "updated_at": l.updated_at} for l in rows]


class LessonPatch(BaseModel):
    status: str | None = None
    lesson: str | None = None


@router.patch("/learning/lessons/{lid}")
async def patch_lesson(lid: int, body: LessonPatch, db: AsyncSession = Depends(get_db),
                 admin=Depends(auth.require_admin)):
    l = await db.get(PlaybookLesson, lid)
    if not l:
        raise HTTPException(404)
    if body.status in ("active", "rejected", "proposed"):
        l.status = body.status
    if body.lesson:
        l.lesson = body.lesson[:1000]
    l.updated_at = utcnow()
    await db.commit()
    return {"ok": True}


@router.get("/learning/calibration")
async def calibration(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Problem.id, Problem.title, Problem.difficulty, Problem.elo, Problem.attempts)
                             .where(Problem.attempts > 0).order_by(Problem.attempts.desc()).limit(100))).all()
    return [dict(r._mapping) for r in rows]
