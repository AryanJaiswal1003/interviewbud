import asyncio
import logging
import statistics
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile

from .. import auth
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent import planner, resume
from ..agent.memory import new_memory
from ..db import (Candidate, CodeRun, Feedback, Interview, Problem, SessionLocal, Turn, Voice, WorkspaceSnapshot,
                  get_db)
from ..evaluation import scorecard
from ..session import RUNTIMES

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/interviews", tags=["interviews"])


async def prepare(interview_id: UUID) -> None:
    """Resume -> profile -> personalised plan. Runs in the background after upload."""
    async with SessionLocal() as db:
        iv = await db.get(Interview, interview_id)
        cand = await db.get(Candidate, iv.candidate_id)
        try:
            iv.status = "planning"
            await db.commit()
            profile = await resume.build_profile(cand.resume_text)
            cand.profile = profile.model_dump()
            cand.name = cand.name or profile.name
            cand.email = cand.email or profile.email
            if iv.seniority == "auto":
                iv.seniority = profile.seniority_estimate
            await db.commit()
            plan = await planner.make_plan(db, iv, cand)
            iv.plan = plan.model_dump()
            iv.problem_id = plan.coding_problem_id
            iv.memory = new_memory()
            iv.status = "ready"
            await db.commit()
        except Exception as e:
            log.exception("prepare failed")
            iv.status = "failed"
            iv.error = str(e)
            await db.commit()


async def owned(db: AsyncSession, interview_id: UUID, user) -> Interview:
    """The interview if this user owns it; 404 otherwise (so ids of other people's interviews aren't confirmed)."""
    iv = await db.get(Interview, interview_id)
    if not iv or iv.owner_id != user.id:
        raise HTTPException(404)
    return iv


@router.post("")
async def create_interview(
    background: BackgroundTasks,
    resume_file: UploadFile = File(...),
    role: str = Form(...),
    seniority: str = Form("auto"),
    duration_min: int = Form(45),
    focus: str = Form(""),
    voice_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user=Depends(auth.current_user),
):
    auth.throttled(auth.create_throttle, f"user:{user.id}", "interviews created")
    data = await resume_file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(413, "Resume too large (max 8 MB)")
    try:
        text = resume.extract_text(resume_file.filename or "resume.pdf", data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not voice_id:
        default = await db.scalar(select(Voice).where(Voice.is_default.is_(True)))
        voice_id = default.fish_model_id if default else ""
    cand = Candidate(resume_filename=resume_file.filename or "", resume_text=text)
    db.add(cand)
    await db.flush()
    iv = Interview(owner_id=user.id, candidate_id=cand.id, role=role.strip()[:200], seniority=seniority,
                   duration_min=max(15, min(90, duration_min)), focus=focus[:6000], voice_id=voice_id)
    db.add(iv)
    await db.commit()
    background.add_task(prepare, iv.id)
    return {"id": str(iv.id), "status": iv.status}


@router.post("/{interview_id}/retry")
async def retry(interview_id: UUID, background: BackgroundTasks, db: AsyncSession = Depends(get_db),
                 user=Depends(auth.current_user)):
    iv = await owned(db, interview_id, user)
    if iv.status not in ("failed", "created"):
        raise HTTPException(409, f"status is {iv.status}")
    iv.error = ""
    await db.commit()
    background.add_task(prepare, iv.id)
    return {"ok": True}


def _summary_row(iv: Interview, cand: Candidate) -> dict:
    card = iv.scorecard or {}
    dims = {d["dimension"]: d["score"] for d in card.get("dimensions", [])}
    dims.update({k: v for k, v in (iv.reviewer_overrides or {}).get("scores", {}).items()})
    scored = [v for v in dims.values() if v]
    return {
        "id": str(iv.id),
        "candidate": cand.name or cand.resume_filename,
        "headline": (cand.profile or {}).get("headline", ""),
        "role": iv.role,
        "seniority": iv.seniority,
        "status": iv.status,
        "recommendation": (iv.reviewer_overrides or {}).get("recommendation") or card.get("recommendation"),
        "scores": dims,
        "avg_score": round(sum(scored) / len(scored), 2) if scored else None,
        "duration_min": iv.duration_min,
        "created_at": iv.created_at,
        "started_at": iv.started_at,
        "ended_at": iv.ended_at,
        "error": iv.error,
    }


@router.get("")
async def list_interviews(db: AsyncSession = Depends(get_db),
                 user=Depends(auth.current_user)):
    rows = (await db.execute(select(Interview, Candidate).join(Candidate).where(Interview.owner_id == user.id)
                             .order_by(Interview.created_at.desc()))).all()
    return [_summary_row(iv, c) for iv, c in rows]


@router.get("/stats")
async def stats(db: AsyncSession = Depends(get_db),
                 user=Depends(auth.current_user)):
    rows = (await db.execute(select(Interview, Candidate).join(Candidate).where(Interview.owner_id == user.id))).all()
    by_status: dict[str, int] = {}
    recs: dict[str, int] = {}
    dim_scores: dict[str, list[int]] = {}
    for iv, c in rows:
        by_status[iv.status] = by_status.get(iv.status, 0) + 1
        r = _summary_row(iv, c)
        if r["recommendation"]:
            recs[r["recommendation"]] = recs.get(r["recommendation"], 0) + 1
        for k, v in r["scores"].items():
            if v:
                dim_scores.setdefault(k, []).append(v)
    metrics = (await db.scalars(
        select(Turn.metrics).where(Turn.speaker == "interviewer").order_by(Turn.id.desc()).limit(2000))).all()

    def pct(vals: list[float], q: float):
        if not vals:
            return None
        vals = sorted(vals)
        return round(vals[min(len(vals) - 1, int(q * len(vals)))])

    lat = {}
    for key in ("first_audio_ms", "stt_ms", "llm_ttft_ms", "voice_to_voice_ms"):
        vals = [m[key] for m in metrics if isinstance(m, dict) and isinstance(m.get(key), (int, float))]
        lat[key] = {"p50": pct(vals, 0.5), "p90": pct(vals, 0.9), "n": len(vals)}
    ratings = (await db.scalars(select(Feedback.rating).where(Feedback.source == "candidate",
                                                              Feedback.rating.is_not(None)))).all()
    return {
        "total": len(rows),
        "by_status": by_status,
        "recommendations": recs,
        "dimension_avgs": {k: round(statistics.mean(v), 2) for k, v in dim_scores.items()},
        "latency": lat,
        "candidate_rating": {"avg": round(statistics.mean(ratings), 2) if ratings else None, "n": len(ratings)},
    }


@router.get("/{interview_id}")
async def get_interview(interview_id: UUID, db: AsyncSession = Depends(get_db),
                 user=Depends(auth.current_user)):
    iv = await owned(db, interview_id, user)
    cand = await db.get(Candidate, iv.candidate_id)
    turns = (await db.scalars(select(Turn).where(Turn.interview_id == iv.id).order_by(Turn.seq))).all()
    runs = (await db.scalars(select(CodeRun).where(CodeRun.interview_id == iv.id).order_by(CodeRun.id))).all()
    snap = await db.get(WorkspaceSnapshot, iv.id)
    problem = await db.get(Problem, iv.problem_id) if iv.problem_id else None
    fb = (await db.scalars(select(Feedback).where(Feedback.interview_id == iv.id))).all()
    return {
        **_summary_row(iv, cand),
        "profile": cand.profile,
        "resume_filename": cand.resume_filename,
        "focus": iv.focus,
        "plan": iv.plan,
        "memory": iv.memory,
        "scorecard": iv.scorecard,
        "critique": iv.critique,
        "reviewer_overrides": iv.reviewer_overrides,
        "problem": {"id": problem.id, "title": problem.title, "difficulty": problem.difficulty, "tags": problem.tags,
                    "elo": round(problem.elo)} if problem else None,
        "turns": [{"seq": t.seq, "speaker": t.speaker, "text": t.text, "phase": t.phase, "channel": t.channel,
                   "metrics": t.metrics, "at": t.created_at} for t in turns],
        "code_runs": [{"id": r.id, "mode": r.mode, "summary": {k: v for k, v in r.summary.items()
                                                               if k != "first_failure"},
                       "first_failure": r.summary.get("first_failure"), "code": r.code, "at": r.created_at}
                      for r in runs],
        "workspace": {"code": snap.code, "notes": snap.notes, "whiteboard": snap.whiteboard} if snap else None,
        "feedback": [{"source": f.source, "rating": f.rating, "comment": f.comment, "at": f.created_at} for f in fb],
        "live": iv.id in RUNTIMES,
        "prep_id": str(iv.prep_id) if iv.prep_id else None,
        "plan_version": iv.plan_version,
    }


@router.post("/{interview_id}/end")
async def end_interview(interview_id: UUID, db: AsyncSession = Depends(get_db),
                 user=Depends(auth.current_user)):
    iv = await owned(db, interview_id, user)
    rt = RUNTIMES.get(interview_id)
    if rt:
        await rt.end_now()
        return {"ok": True}
    if iv.status == "live":
        iv.status = "completed"
        await db.commit()
        asyncio.create_task(scorecard.finalize(iv.id))
    return {"ok": True}


@router.post("/{interview_id}/rescore")
async def rescore(interview_id: UUID, db: AsyncSession = Depends(get_db),
                 user=Depends(auth.current_user)):
    iv = await owned(db, interview_id, user)
    if iv.status not in ("completed", "scored"):
        raise HTTPException(409, "interview not finished")
    asyncio.create_task(scorecard.finalize(iv.id))
    return {"ok": True}


class FeedbackIn(BaseModel):
    rating: int | None = None
    comment: str = ""


@router.post("/{interview_id}/feedback")
async def candidate_feedback(interview_id: UUID, body: FeedbackIn, db: AsyncSession = Depends(get_db),
                 user=Depends(auth.current_user)):
    await owned(db, interview_id, user)
    db.add(Feedback(interview_id=interview_id, source="candidate", rating=body.rating, comment=body.comment[:4000]))
    await db.commit()
    return {"ok": True}


class ReviewIn(BaseModel):
    scores: dict[str, int] = {}
    recommendation: str | None = None
    note: str = ""


@router.post("/{interview_id}/review")
async def reviewer_override(interview_id: UUID, body: ReviewIn, db: AsyncSession = Depends(get_db),
                 user=Depends(auth.current_user)):
    iv = await owned(db, interview_id, user)
    iv.reviewer_overrides = {"scores": {k: max(0, min(4, v)) for k, v in body.scores.items()},
                             "recommendation": body.recommendation, "note": body.note}
    model_scores = {d["dimension"]: d["score"] for d in (iv.scorecard or {}).get("dimensions", [])}
    disagreements = {k: {"model": model_scores.get(k), "reviewer": v} for k, v in body.scores.items()
                     if model_scores.get(k) != v}
    db.add(Feedback(interview_id=iv.id, source="reviewer", comment=body.note[:4000],
                    data={"disagreements": disagreements, "recommendation": body.recommendation}))
    await db.commit()
    return {"ok": True, "disagreements": disagreements}


@router.delete("/{interview_id}")
async def delete_interview(interview_id: UUID, db: AsyncSession = Depends(get_db),
                 user=Depends(auth.current_user)):
    iv = await owned(db, interview_id, user)
    if interview_id in RUNTIMES:
        raise HTTPException(409, "interview is live")
    cand = await db.get(Candidate, iv.candidate_id)
    await db.delete(iv)
    others = await db.scalar(select(func.count()).where(Interview.candidate_id == cand.id, Interview.id != iv.id))
    if not others:
        await db.delete(cand)
    await db.commit()
    return {"ok": True}
