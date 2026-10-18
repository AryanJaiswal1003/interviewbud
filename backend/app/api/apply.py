"""Applying on the candidate's behalf (see apply/service.py for the steps), the application details it fills from,
and similar jobs. Slow steps (reading, filling, submitting) run in the background; the page polls."""

import re
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import auth
from ..apply import service as apply
from ..db import Application, User, get_db
from ..prep import jobs, service
from .prep import owned_prep

router = APIRouter(prefix="/api", tags=["apply"])
fill_throttle = auth.Throttle(limit=30, window=3600)  # browser sessions per user per hour


def view(app: Application) -> dict:
    fr = app.fill_report or {}
    return {
        "id": str(app.id), "status": app.status, "ats": app.ats, "apply_url": app.apply_url, "error": app.error,
        "fields": [{k: f.get(k) for k in ("key", "label", "type", "kind", "required", "options", "search",
                                           "maxlength", "description")} for f in app.fields],
        "values": app.values, "approved": app.approved or None,
        "approved_at": app.approved_at.isoformat() if app.approved_at else None,
        "filled_at": fr.get("filled_at"), "fill_fields": fr.get("fields") or {}, "screenshot": fr.get("screenshot"),
        "submit_button": fr.get("submit_button", ""), "confirmation": app.confirmation,
        "submitted_at": app.submitted_at.isoformat() if app.submitted_at else None,
        "missing_required": apply.unanswered_required(app, app.approved or app.values) if app.fields else [],
        "history": app.history[-20:], "updated_at": app.updated_at.isoformat(),
    }


async def owned_app(db: AsyncSession, app_id: UUID, user: User) -> Application:
    app = await db.get(Application, app_id)
    if not app or app.owner_id != user.id:
        raise HTTPException(404)
    return app


@router.get("/prep/{prep_id}/application")
async def get_application(prep_id: UUID, user: User = Depends(auth.current_user), db: AsyncSession = Depends(get_db)):
    await owned_prep(db, prep_id, user)
    app = await db.scalar(select(Application).where(Application.prep_id == prep_id)
                          .order_by(Application.created_at.desc()).limit(1))
    return view(app) if app else None


@router.post("/prep/{prep_id}/application")
async def read_application(prep_id: UUID, user: User = Depends(auth.current_user), db: AsyncSession = Depends(get_db)):
    """Open the job's real application form and propose values (nothing is filled or sent)."""
    prep = await owned_prep(db, prep_id, user)
    if prep.status in ("intake", "needs_text", "failed"):
        raise HTTPException(409, "The job posting needs to be read first")
    auth.throttled(fill_throttle, f"user:{user.id}", "form sessions")
    app = await apply.start(db, prep)
    return view(app)


@router.post("/applications/{app_id}/remap")
async def remap(app_id: UUID, user: User = Depends(auth.current_user), db: AsyncSession = Depends(get_db)):
    """Propose values again (e.g. after the screening call or after updating application details)."""
    app = await owned_app(db, app_id, user)
    if app.status in ("reading", "filling", "submitting", "submitted") or not app.fields:
        raise HTTPException(409, "Not now")
    await apply.map_values(db, app)
    app.approved, app.approved_at = {}, None
    await db.commit()
    return view(app)


class ApproveIn(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)


@router.post("/applications/{app_id}/approve")
async def approve(app_id: UUID, body: ApproveIn, user: User = Depends(auth.current_user),
                  db: AsyncSession = Depends(get_db)):
    """The candidate's reviewed values become the approved snapshot. Anything changed later needs approving again."""
    app = await owned_app(db, app_id, user)
    if app.status in ("reading", "filling", "submitting", "submitted"):
        raise HTTPException(409, "Not now")
    try:
        final = apply.apply_edits(app, {k: str(v)[:6000] for k, v in body.values.items()})
    except apply.ApproveError as e:
        raise HTTPException(400, str(e))
    app.values = final
    app.approved = final
    app.approved_at = apply.now()
    app.status = "approved"
    app.error = ""
    apply.log_event(app, "approved", f"{sum(1 for v in final.values() if v['value'])} values")
    await db.commit()
    return view(app)


@router.post("/applications/{app_id}/fill")
async def fill(app_id: UUID, user: User = Depends(auth.current_user), db: AsyncSession = Depends(get_db)):
    """Fill the real form with the approved values and take a screenshot. Does not submit."""
    app = await owned_app(db, app_id, user)
    if not app.approved or app.status not in ("approved", "filled", "needs_you", "failed"):
        raise HTTPException(409, "Approve the answers first")
    auth.throttled(fill_throttle, f"user:{user.id}", "form sessions")
    app.status, app.error = "filling", ""
    apply.log_event(app, "fill_requested")
    await db.commit()
    apply.spawn(apply.fill(app.id))
    return view(app)


class SubmitIn(BaseModel):
    confirm: bool = False


@router.post("/applications/{app_id}/submit")
async def submit(app_id: UUID, body: SubmitIn, user: User = Depends(auth.current_user),
                 db: AsyncSession = Depends(get_db)):
    """Send the application. Only on an explicit confirmation, only after a fill preview of the approved values."""
    app = await owned_app(db, app_id, user)
    if not body.confirm:
        raise HTTPException(400, "Confirm that you want to send the application")
    if app.status != "filled" or not app.approved_at:
        raise HTTPException(409, "Preview the filled form first")
    filled_at = (app.fill_report or {}).get("filled_at")
    if not filled_at or datetime.fromisoformat(filled_at) < app.approved_at:
        raise HTTPException(409, "Your answers changed after the preview; preview the filled form again")
    missing = apply.unanswered_required(app, app.approved)
    if missing:
        raise HTTPException(409, "Required questions are still empty: " + "; ".join(missing[:5]))
    failed = [k for k, v in ((app.fill_report or {}).get("fields") or {}).items() if v != "filled"]
    if failed:
        raise HTTPException(409, f"{len(failed)} fields couldn't be filled in the preview; fix them or apply on the "
                                 "site yourself")
    auth.throttled(fill_throttle, f"user:{user.id}", "form sessions")
    app.status = "submitting"
    apply.log_event(app, "submit_requested", "confirmed by the candidate")
    await db.commit()
    apply.spawn(apply.submit(app.id))
    return view(app)


@router.get("/applications/{app_id}/screenshot/{name}")
async def screenshot(app_id: UUID, name: str, user: User = Depends(auth.current_user),
                     db: AsyncSession = Depends(get_db)):
    app = await owned_app(db, app_id, user)
    if name not in ("filled", "submitted"):
        raise HTTPException(404)
    p = apply.shot_path(app, name)
    if not p.exists():
        raise HTTPException(404)
    return Response(p.read_bytes(), media_type="image/png", headers={"Cache-Control": "private, no-store"})


# ---------------- application details ----------------
class DetailsIn(BaseModel):
    details: dict[str, str]


@router.get("/profile/application")
async def get_details(user: User = Depends(auth.current_user)):
    meta = (user.preferences or {}).get("resume_file")
    return {"details": apply.details_for(user), "labels": apply.DETAIL_LABELS,
            "resume": {k: meta[k] for k in ("name", "size", "uploaded_at")} if meta else None}


@router.put("/profile/application")
async def put_details(body: DetailsIn, user: User = Depends(auth.current_user), db: AsyncSession = Depends(get_db)):
    d = {k: str(body.details.get(k, "")).strip()[:300] for k in apply.DETAIL_KEYS}
    if d["email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", d["email"]):
        raise HTTPException(400, "That email doesn't look right")
    for k in ("linkedin", "github", "website"):
        if d[k] and not re.match(r"^https?://", d[k]):
            d[k] = "https://" + d[k]
    u = await db.get(User, user.id)
    u.preferences = {**(u.preferences or {}), "applicant": d}
    await db.commit()
    return await get_details(u)


@router.post("/profile/application/resume")
async def put_resume(file: UploadFile = File(...), user: User = Depends(auth.current_user),
                     db: AsyncSession = Depends(get_db)):
    """The resume file attached to applications (separate from importing it as profile suggestions)."""
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(413, "Resume too large (max 8 MB)")
    if not (data[:4] == b"%PDF" or data[:2] == b"PK" or data[:4] == b"\xd0\xcf\x11\xe0" or file.filename.lower()
            .endswith((".txt", ".rtf"))):
        raise HTTPException(400, "Upload a PDF, Word or text file")
    u = await db.get(User, user.id)
    try:
        meta = apply.save_resume(u, file.filename or "resume.pdf", data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    u.preferences = {**(u.preferences or {}), "resume_file": meta}
    await db.commit()
    return {"name": meta["name"], "size": meta["size"], "uploaded_at": meta["uploaded_at"]}


# ---------------- similar jobs ----------------
@router.get("/prep/{prep_id}/jobs")
async def similar_jobs(prep_id: UUID, user: User = Depends(auth.current_user), db: AsyncSession = Depends(get_db)):
    prep = await owned_prep(db, prep_id, user)
    sj = prep.similar_jobs or {}
    if (not sj or sj.get("status") == "working") and not jobs.running(prep.id) and prep.status not in (
            "intake", "needs_text", "failed"):
        service.spawn(jobs.generate(prep.id))
        sj = {**sj, "status": "working"}
    return sj


@router.post("/prep/{prep_id}/jobs/refresh")
async def refresh_jobs(prep_id: UUID, user: User = Depends(auth.current_user), db: AsyncSession = Depends(get_db)):
    prep = await owned_prep(db, prep_id, user)
    auth.throttled(auth.create_throttle, f"user:{user.id}", "job searches")
    if not jobs.running(prep.id):
        service.spawn(jobs.generate(prep.id))
    return {"status": "working"}
