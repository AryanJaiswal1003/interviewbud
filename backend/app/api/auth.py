"""Sign up, log in, profile and devices. Email + password for now."""

import re
import zlib
from zoneinfo import available_timezones

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import auth
from ..db import AuthSession, Candidate, Interview, User, get_db, utcnow
from ..session import RUNTIMES

router = APIRouter(prefix="/api/auth", tags=["auth"])

ACCENTS = {"amber", "marine", "coral", "grey"}
SENIORITIES = {"auto", "intern", "junior", "mid", "senior", "staff"}
NAME_RE = re.compile(r"^[\w .'\-]{1,40}$", re.UNICODE)
AVATAR_TYPES = {"image/png", "image/jpeg", "image/webp"}


def me_view(u: User) -> dict:
    return {
        "id": str(u.id), "email": u.email, "display_name": u.display_name, "headline": u.headline,
        "accent": u.accent, "timezone": u.timezone, "preferences": u.preferences or {}, "is_admin": u.is_admin,
        "avatar_url": f"/api/auth/me/avatar?v={zlib.crc32(u.avatar):x}" if u.avatar else None,
        "created_at": auth.utc(u.created_at),
    }


class SignupIn(BaseModel):
    email: EmailStr
    password: str
    display_name: str = Field("", max_length=120)
    remember: bool = False


class LoginIn(BaseModel):
    email: EmailStr
    password: str
    remember: bool = False


@router.post("/signup")
async def signup(body: SignupIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    email = body.email.strip().lower()
    auth.throttled(auth.signup_throttle, f"ip:{auth.client_ip(request)}", "sign-ups from this network")
    if problem := auth.password_problem(body.password, email):
        raise HTTPException(400, problem)
    if await db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists. Log in instead.")
    user = User(email=email, password_hash=auth.hash_password(body.password),
                display_name=body.display_name.strip() or email.split("@")[0],
                preferences={"theme": "dark", "interviewer_name": "Alex"})
    db.add(user)
    await db.flush()
    # The first person to sign up administers the install, and owns interviews made before accounts existed.
    if await db.scalar(select(func.count()).select_from(User)) == 1:
        user.is_admin = True
        await db.execute(update(Interview).where(Interview.owner_id.is_(None)).values(owner_id=user.id))
    await auth.create_session(db, user, body.remember, request, response)
    return me_view(user)


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    email = body.email.strip().lower()
    keys = (f"email:{email}", f"ip:{auth.client_ip(request)}")
    if wait := auth.login_throttle.blocked(*keys):
        raise HTTPException(429, f"Too many attempts. Try again in {int(wait // 60) + 1} minutes.")
    user = await db.scalar(select(User).where(User.email == email))
    if not auth.verify_password(user, body.password):
        auth.login_throttle.fail(*keys)
        raise HTTPException(401, "That email and password don't match.")
    auth.login_throttle.reset(keys[0])
    if auth._ph.check_needs_rehash(user.password_hash):
        user.password_hash = auth.hash_password(body.password)
    await auth.create_session(db, user, body.remember, request, response)
    return me_view(user)


@router.post("/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    found = await auth.session_for_token(db, request.cookies.get(auth.COOKIE))
    if found:
        await auth.revoke_session(db, found[0].id, found[1].id)
    auth.clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
async def me(user: User = Depends(auth.current_user)):
    return me_view(user)


class ProfileIn(BaseModel):
    display_name: str | None = Field(None, max_length=120)
    headline: str | None = Field(None, max_length=200)
    accent: str | None = None
    timezone: str | None = None
    preferences: dict | None = None


@router.patch("/me")
async def update_me(body: ProfileIn, user: User = Depends(auth.current_user), db: AsyncSession = Depends(get_db)):
    if body.display_name is not None:
        user.display_name = body.display_name.strip() or user.display_name
    if body.headline is not None:
        user.headline = body.headline.strip()
    if body.accent is not None:
        if body.accent not in ACCENTS:
            raise HTTPException(400, "Unknown colour")
        user.accent = body.accent
    if body.timezone is not None:
        if body.timezone and body.timezone not in available_timezones():
            raise HTTPException(400, "Unknown timezone")
        user.timezone = body.timezone
    if body.preferences is not None:
        prefs = dict(user.preferences or {})
        p = body.preferences
        if "interviewer_name" in p:
            name = str(p["interviewer_name"]).strip()
            if not NAME_RE.match(name):
                raise HTTPException(400, "The interviewer's name can use letters, spaces, apostrophes and hyphens")
            prefs["interviewer_name"] = name
        if "default_role" in p:
            prefs["default_role"] = str(p["default_role"]).strip()[:200]
        if "default_seniority" in p:
            if p["default_seniority"] not in SENIORITIES:
                raise HTTPException(400, "Unknown level")
            prefs["default_seniority"] = p["default_seniority"]
        if "default_duration" in p:
            if p["default_duration"] not in (30, 45, 60):
                raise HTTPException(400, "Length must be 30, 45 or 60 minutes")
            prefs["default_duration"] = p["default_duration"]
        if "default_voice" in p:
            prefs["default_voice"] = str(p["default_voice"])[:64]
        if "theme" in p:
            if p["theme"] not in ("dark", "light"):
                raise HTTPException(400, "Theme must be dark or light")
            prefs["theme"] = p["theme"]
        if "analytics" in p:
            prefs["analytics"] = bool(p["analytics"])
        user.preferences = prefs
    await db.commit()
    return me_view(user)


@router.put("/me/avatar")
async def put_avatar(file: UploadFile = File(...), user: User = Depends(auth.current_user),
                     db: AsyncSession = Depends(get_db)):
    if file.content_type not in AVATAR_TYPES:
        raise HTTPException(400, "Use a PNG, JPEG or WebP image")
    data = await file.read()
    if len(data) > 2 * 1024 * 1024:
        raise HTTPException(413, "Image too large (max 2 MB)")
    # Trust the bytes, not the declared type: we serve this back with that type.
    kind = ("image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg" if data.startswith(b"\xff\xd8\xff")
            else "image/webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else None)
    if kind is None:
        raise HTTPException(400, "That file isn't a PNG, JPEG or WebP image")
    user.avatar, user.avatar_type = data, kind
    await db.commit()
    return me_view(user)


@router.delete("/me/avatar")
async def delete_avatar(user: User = Depends(auth.current_user), db: AsyncSession = Depends(get_db)):
    user.avatar, user.avatar_type = None, ""
    await db.commit()
    return me_view(user)


@router.get("/me/avatar")
async def get_avatar(user: User = Depends(auth.current_user)):
    if not user.avatar:
        raise HTTPException(404)
    return Response(user.avatar, media_type=user.avatar_type, headers={"Cache-Control": "private, max-age=86400"})


class PasswordIn(BaseModel):
    current: str
    new: str


@router.post("/me/password")
async def change_password(body: PasswordIn, found=Depends(auth.current_session), db: AsyncSession = Depends(get_db)):
    sess, user = found
    if not auth.verify_password(user, body.current):
        raise HTTPException(400, "Your current password isn't right.")
    if problem := auth.password_problem(body.new, user.email):
        raise HTTPException(400, problem)
    user.password_hash = auth.hash_password(body.new)
    # A new password signs out every other device.
    await db.execute(update(AuthSession).where(AuthSession.user_id == user.id, AuthSession.id != sess.id,
                                               AuthSession.revoked_at.is_(None)).values(revoked_at=utcnow()))
    await db.commit()
    return {"ok": True}


@router.get("/sessions")
async def sessions(found=Depends(auth.current_session), db: AsyncSession = Depends(get_db)):
    current, user = found
    return [{"id": s.id, "current": s.id == current.id, "remember": s.remember, "user_agent": s.user_agent,
             "ip": s.ip, "created_at": auth.utc(s.created_at), "last_seen_at": auth.utc(s.last_seen_at),
             "expires_at": auth.utc(s.expires_at)} for s in await auth.user_sessions(db, user.id)]


@router.delete("/sessions/{session_id}")
async def revoke(session_id: str, response: Response, found=Depends(auth.current_session),
                 db: AsyncSession = Depends(get_db)):
    current, user = found
    if not await auth.revoke_session(db, session_id, user.id):
        raise HTTPException(404)
    if session_id == current.id:
        auth.clear_session_cookie(response)
    return {"ok": True}


@router.post("/sessions/revoke-others")
async def revoke_others(found=Depends(auth.current_session), db: AsyncSession = Depends(get_db)):
    current, user = found
    res = await db.execute(update(AuthSession).where(AuthSession.user_id == user.id, AuthSession.id != current.id,
                                                     AuthSession.revoked_at.is_(None)).values(revoked_at=utcnow()))
    await db.commit()
    return {"ok": True, "signed_out": res.rowcount}


class DeleteIn(BaseModel):
    password: str


@router.post("/me/delete")
async def delete_account(body: DeleteIn, response: Response, user: User = Depends(auth.current_user),
                         db: AsyncSession = Depends(get_db)):
    if not auth.verify_password(user, body.password):
        raise HTTPException(400, "Your password isn't right.")
    ivs = (await db.scalars(select(Interview).where(Interview.owner_id == user.id))).all()
    if any(iv.id in RUNTIMES for iv in ivs):
        raise HTTPException(409, "End your live interview first.")
    cand_ids = [iv.candidate_id for iv in ivs]
    await db.delete(user)  # cascades: sessions, interviews (and their turns, runs, snapshots)
    await db.flush()
    if cand_ids:
        await db.execute(delete(Candidate).where(Candidate.id.in_(cand_ids)))
    await db.commit()
    auth.clear_session_cookie(response)
    return {"ok": True}


@router.get("/agent-token")
async def get_agent_token(user: User = Depends(auth.current_user)):
    """For CopilotKit runs: the runtime calls our agents server-to-server, without the browser's cookie."""
    return {"token": auth.agent_token(user.id)}
