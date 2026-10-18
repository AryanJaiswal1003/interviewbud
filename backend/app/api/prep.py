"""Preparation journey API: job URL -> candidate context -> plan -> practice -> feedback. Slow model steps run in the
background; the page polls GET /api/prep/{id}, which always reflects the saved state (refresh-safe)."""

from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import auth
from ..agent import resume
from ..db import (Interview, Opportunity, PrepPlan, PrepQuestion, PrepSession, ProfileFact, ProfileFactRevision,
                  SessionLocal, Turn, User, get_db)
from ..prep import fetcher, service

router = APIRouter(prefix="/api", tags=["prep"])
_reviewing: set[str] = set()


async def owned_prep(db: AsyncSession, prep_id: UUID, user: User) -> PrepSession:
    prep = await db.get(PrepSession, prep_id)
    if not prep or prep.owner_id != user.id:
        raise HTTPException(404)
    return prep


class NewPrep(BaseModel):
    url: str = Field("", max_length=2000)
    text: str = Field("", max_length=60000)


@router.post("/prep")
async def create_prep(body: NewPrep, user: User = Depends(auth.current_user), db: AsyncSession = Depends(get_db)):
    if not body.url.strip() and len(body.text.strip()) < 200:
        raise HTTPException(400, "Paste a job link, or the job description (at least a few paragraphs)")
    auth.throttled(auth.create_throttle, f"user:{user.id}", "preparation sessions")
    url = ""
    if body.url.strip():
        try:
            url = fetcher.check_url(body.url)
        except fetcher.FetchRefused as e:
            raise HTTPException(400, str(e))
    has_text = len(body.text.strip()) >= 200
    opp = Opportunity(owner_id=user.id, source_url=url, source_kind="text" if has_text and not url else "url",
                      source_text=body.text.strip()[:60000] if has_text else "",
                      fetch_method="pasted" if has_text else "", fetch_status="ok" if has_text else "pending")
    db.add(opp)
    await db.flush()
    prep = PrepSession(owner_id=user.id, opportunity_id=opp.id, status="intake", pending_action="Reading the posting")
    db.add(prep)
    await db.commit()
    service.spawn(service.run_intake(prep.id))
    return {"id": str(prep.id)}


@router.get("/prep")
async def list_preps(user: User = Depends(auth.current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(PrepSession, Opportunity).join(Opportunity)
                             .where(PrepSession.owner_id == user.id).order_by(PrepSession.updated_at.desc()))).all()
    out = []
    for p, o in rows:
        out.append({"id": str(p.id), "title": o.title or o.extraction.get("title") or o.source_url or "Job description",
                    "company": o.company, "source_url": o.source_url, "status": p.status,
                    "pending_action": p.pending_action, "updated_at": p.updated_at.isoformat(),
                    "practices": await service.practice_count(db, p.id)})
    return out


@router.get("/prep/{prep_id}")
async def get_prep(prep_id: UUID, user: User = Depends(auth.current_user), db: AsyncSession = Depends(get_db)):
    prep = await owned_prep(db, prep_id, user)
    opp = await db.get(Opportunity, prep.opportunity_id)
    questions = (await db.scalars(select(PrepQuestion).where(PrepQuestion.prep_id == prep.id)
                                  .order_by(PrepQuestion.id))).all()
    plans = (await db.scalars(select(PrepPlan).where(PrepPlan.prep_id == prep.id)
                              .order_by(PrepPlan.version.desc()))).all()
    ivs = (await db.scalars(select(Interview).where(Interview.prep_id == prep.id)
                            .order_by(Interview.created_at.desc()))).all()
    # Feedback is generated once an interview is scored; kick it off the first time someone looks.
    for iv in ivs:
        key = str(iv.id)
        if iv.status == "scored" and key not in (prep.recommendations or {}) and key not in _reviewing:
            _reviewing.add(key)
            service.spawn(_review(prep.id, iv.id))
    facts = await service.user_facts(db, user.id)
    return {
        "id": str(prep.id), "status": prep.status, "pending_action": prep.pending_action, "error": prep.error,
        "updated_at": prep.updated_at.isoformat(),
        "opportunity": {
            "source_url": opp.source_url, "final_url": opp.final_url, "source_kind": opp.source_kind,
            "fetch_method": opp.fetch_method, "fetch_status": opp.fetch_status, "fetch_error": opp.fetch_error,
            "title": opp.title, "company": opp.company, "extraction": opp.extraction,
            "interpretation": opp.interpretation, "fetched_at": opp.fetched_at.isoformat() if opp.fetched_at else None,
            "source_chars": len(opp.source_text),
        },
        "coverage": prep.coverage,
        "questions": [{"id": q.id, "requirement_id": q.requirement_id, "question": q.question, "why": q.why,
                       "answer": q.answer, "status": q.status, "fact_id": q.fact_id} for q in questions],
        "facts": [service.fact_view(f) for f in facts],
        "plans": [{"version": p.version, "note": p.note, "created_at": p.created_at.isoformat()} for p in plans],
        "plan": {"version": plans[0].version, **plans[0].plan} if plans else None,
        "practices": [{"id": str(iv.id), "status": iv.status, "plan_version": iv.plan_version,
                       "created_at": iv.created_at.isoformat(), "duration_min": iv.duration_min,
                       "recommendation": (iv.scorecard or {}).get("recommendation"),
                       "scores": {d["dimension"]: d["score"] for d in (iv.scorecard or {}).get("dimensions", [])},
                       "reviewing": str(iv.id) in _reviewing} for iv in ivs],
        "feedback": prep.recommendations or {},
    }


async def _review(prep_id: UUID, interview_id: UUID) -> None:
    try:
        async with SessionLocal() as db:
            prep = await db.get(PrepSession, prep_id)
            iv = await db.get(Interview, interview_id)
            turns = (await db.scalars(select(Turn).where(Turn.interview_id == iv.id).order_by(Turn.seq))).all()
            await service.review_practice(db, prep, iv, list(turns))
    finally:
        _reviewing.discard(str(interview_id))


class PasteIn(BaseModel):
    text: str = Field(..., min_length=200, max_length=60000)


@router.post("/prep/{prep_id}/text")
async def paste_text(prep_id: UUID, body: PasteIn, user: User = Depends(auth.current_user),
                     db: AsyncSession = Depends(get_db)):
    prep = await owned_prep(db, prep_id, user)
    opp = await db.get(Opportunity, prep.opportunity_id)
    opp.source_text, opp.fetch_method, opp.fetch_status, opp.fetch_error = body.text.strip(), "pasted", "ok", ""
    prep.status, prep.pending_action, prep.error = "intake", "Reading the posting", ""
    await db.commit()
    service.spawn(service.run_intake(prep.id))
    return {"ok": True}


@router.post("/prep/{prep_id}/retry")
async def retry(prep_id: UUID, user: User = Depends(auth.current_user), db: AsyncSession = Depends(get_db)):
    prep = await owned_prep(db, prep_id, user)
    prep.status, prep.error, prep.pending_action = "intake", "", "Reading the posting"
    await db.commit()
    service.spawn(service.run_intake(prep.id))
    return {"ok": True}


class AnswerIn(BaseModel):
    answer: str = Field(..., min_length=1, max_length=4000)


@router.post("/prep/{prep_id}/questions/{qid}/answer")
async def answer(prep_id: UUID, qid: int, body: AnswerIn, user: User = Depends(auth.current_user),
                 db: AsyncSession = Depends(get_db)):
    prep = await owned_prep(db, prep_id, user)
    q = await db.get(PrepQuestion, qid)
    if not q or q.prep_id != prep.id or q.status != "open":
        raise HTTPException(404)
    fact = await service.answer_question(db, prep, q, body.answer)
    service.spawn(_refresh(prep.id))
    return {"fact": service.fact_view(fact)}


@router.post("/prep/{prep_id}/questions/{qid}/skip")
async def skip(prep_id: UUID, qid: int, user: User = Depends(auth.current_user), db: AsyncSession = Depends(get_db)):
    prep = await owned_prep(db, prep_id, user)
    q = await db.get(PrepQuestion, qid)
    if not q or q.prep_id != prep.id or q.status != "open":
        raise HTTPException(404)
    q.status = "skipped"
    await db.commit()
    service.spawn(_refresh(prep.id))
    return {"ok": True}


async def _refresh(prep_id: UUID) -> None:
    async with SessionLocal() as db:
        prep = await db.get(PrepSession, prep_id)
        await service.refresh_coverage(db, prep)


class PlanIn(BaseModel):
    request: str = Field("", max_length=2000)


@router.post("/prep/{prep_id}/plan")
async def make_plan(prep_id: UUID, body: PlanIn, user: User = Depends(auth.current_user),
                    db: AsyncSession = Depends(get_db)):
    prep = await owned_prep(db, prep_id, user)
    if prep.status in ("intake", "needs_text", "planning"):
        raise HTTPException(409, "Not ready to plan yet")
    prep.status, prep.pending_action = "planning", "Building your preparation plan"
    await db.commit()

    async def run():
        async with SessionLocal() as s:
            await service.generate_plan(s, await s.get(PrepSession, prep_id), body.request)
    service.spawn(run())
    return {"ok": True}


class DraftIn(BaseModel):
    draft: str = Field(..., max_length=6000)
    approved: bool = False


@router.put("/prep/{prep_id}/drafts/{question_id}")
async def edit_draft(prep_id: UUID, question_id: str, body: DraftIn, user: User = Depends(auth.current_user),
                     db: AsyncSession = Depends(get_db)):
    """Editing or approving an application-answer draft saves a new plan version (earlier versions stay intact)."""
    prep = await owned_prep(db, prep_id, user)
    cur = await service.latest_plan(db, prep.id)
    if not cur:
        raise HTTPException(404)
    plan = {**cur.plan, "answer_drafts": [dict(d) for d in cur.plan.get("answer_drafts", [])]}
    d = next((d for d in plan["answer_drafts"] if d["question_id"] == question_id), None)
    if not d:
        raise HTTPException(404)
    d["draft"], d["approved"], d["edited_by_user"] = body.draft.strip(), body.approved, True
    db.add(PrepPlan(prep_id=prep.id, version=cur.version + 1, plan=plan,
                    note=f"{'approved' if body.approved else 'edited'} draft {question_id}"))
    await db.commit()
    return {"version": cur.version + 1}


class PracticeIn(BaseModel):
    duration_min: int = 15
    plan_version: int | None = None


@router.post("/prep/{prep_id}/practice")
async def practice(prep_id: UUID, body: PracticeIn, user: User = Depends(auth.current_user),
                   db: AsyncSession = Depends(get_db)):
    prep = await owned_prep(db, prep_id, user)
    auth.throttled(auth.create_throttle, f"user:{user.id}", "interviews created")
    plan = (await db.scalar(select(PrepPlan).where(PrepPlan.prep_id == prep.id,
                                                   PrepPlan.version == body.plan_version))
            if body.plan_version else await service.latest_plan(db, prep.id))
    if not plan:
        raise HTTPException(409, "Build a preparation plan first")
    iv = await service.start_practice(db, prep, user, plan, max(10, min(60, body.duration_min)))
    return {"interview_id": str(iv.id)}


@router.delete("/prep/{prep_id}")
async def delete_prep(prep_id: UUID, user: User = Depends(auth.current_user), db: AsyncSession = Depends(get_db)):
    prep = await owned_prep(db, prep_id, user)
    opp_id = prep.opportunity_id
    await db.delete(prep)  # practice interviews keep their results (prep_id is set to NULL)
    await db.execute(delete(Opportunity).where(Opportunity.id == opp_id))
    await db.commit()
    return {"ok": True}


# ---------------- candidate profile facts (shared across preparation sessions) ----------------
class FactIn(BaseModel):
    topic: str = Field(..., min_length=1, max_length=200)
    value: str = Field(..., min_length=1, max_length=4000)


class FactPatch(BaseModel):
    topic: str | None = Field(None, max_length=200)
    value: str | None = Field(None, max_length=4000)
    status: str | None = None


@router.get("/profile/facts")
async def list_facts(user: User = Depends(auth.current_user), db: AsyncSession = Depends(get_db)):
    return [service.fact_view(f) for f in await service.user_facts(db, user.id)]


@router.post("/profile/facts")
async def add_fact(body: FactIn, user: User = Depends(auth.current_user), db: AsyncSession = Depends(get_db)):
    f = ProfileFact(user_id=user.id, topic=body.topic.strip(), value=body.value.strip(), status="confirmed",
                    source="manual")
    db.add(f)
    await db.commit()
    await _refresh_all(user.id)
    return service.fact_view(f)


@router.patch("/profile/facts/{fact_id}")
async def patch_fact(fact_id: int, body: FactPatch, user: User = Depends(auth.current_user),
                     db: AsyncSession = Depends(get_db)):
    f = await db.get(ProfileFact, fact_id)
    if not f or f.user_id != user.id:
        raise HTTPException(404)
    if body.status is not None and body.status not in ("confirmed", "suggested", "retracted"):
        raise HTTPException(400, "Status must be confirmed, suggested or retracted")
    await service.edit_fact(db, f, value=body.value, status=body.status, topic=body.topic,
                            reason="confirm" if body.status == "confirmed" and body.value is None else "edit")
    await _refresh_all(user.id)
    return service.fact_view(f)


@router.get("/profile/facts/{fact_id}/revisions")
async def fact_revisions(fact_id: int, user: User = Depends(auth.current_user), db: AsyncSession = Depends(get_db)):
    f = await db.get(ProfileFact, fact_id)
    if not f or f.user_id != user.id:
        raise HTTPException(404)
    revs = (await db.scalars(select(ProfileFactRevision).where(ProfileFactRevision.fact_id == fact_id)
                             .order_by(ProfileFactRevision.id))).all()
    return [{"old_value": r.old_value, "new_value": r.new_value, "old_status": r.old_status,
             "new_status": r.new_status, "reason": r.reason, "at": r.at.isoformat()} for r in revs]


@router.post("/profile/resume")
async def upload_resume(file: UploadFile = File(...), user: User = Depends(auth.current_user),
                        db: AsyncSession = Depends(get_db)):
    """Resume facts come in as SUGGESTED: they're only used once the user confirms them."""
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(413, "Resume too large (max 8 MB)")
    try:
        text = resume.extract_text(file.filename or "resume.pdf", data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    n = await service.import_resume(db, user.id, text)
    await _refresh_all(user.id)
    return {"suggested": n}


async def _refresh_all(user_id: UUID) -> None:
    """Facts changed: re-check coverage for this user's journeys that are still gathering context."""
    async with SessionLocal() as db:
        ids = (await db.scalars(select(PrepSession.id).where(PrepSession.owner_id == user_id,
                                                             PrepSession.status == "context"))).all()
    for pid in ids:
        service.spawn(_refresh(pid))
