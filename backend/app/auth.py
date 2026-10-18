"""Accounts and sessions.

* Passwords: Argon2id (argon2-cffi defaults), rehashed on login when parameters change.
* Sessions: a random 256-bit token in an HttpOnly, SameSite=Lax cookie (`ib_session`); the database keeps only its
  SHA-256, so a leaked table can't be replayed. "Remember this device" makes the cookie persistent for
  SESSION_REMEMBER_DAYS (sliding); otherwise it is a browser-session cookie with a SESSION_HOURS server lifetime.
* CSRF: SameSite=Lax plus an Origin check on every state-changing request (see `origin_guard`).
* Agents: the CopilotKit runtime calls /agui/* server-to-server without the browser's cookies, so the browser passes
  a short-lived HMAC-signed agent token (`agent_token`) in the run's forwarded properties instead.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Response, WebSocket
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .config import BACKEND_DIR, get_settings
from .db import AuthSession, SessionLocal, User, get_db, utcnow

COOKIE = "ib_session"
_ph = PasswordHasher()
# Hash of a random password, verified against when the email doesn't exist so timing doesn't reveal accounts.
_DUMMY_HASH = _ph.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(user: User | None, password: str) -> bool:
    try:
        return _ph.verify(user.password_hash if user else _DUMMY_HASH, password) and user is not None
    except (VerifyMismatchError, InvalidHashError):
        return False


def password_problem(password: str, email: str = "") -> str | None:
    if len(password) < 8:
        return "Use at least 8 characters."
    if len(password) > 128:
        return "Use at most 128 characters."
    if email and password.lower() == email.lower():
        return "Don't use your email address as the password."
    return None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def client_ip(request: Request | WebSocket) -> str:
    return (request.client.host if request.client else "")[:64]


async def create_session(db: AsyncSession, user: User, remember: bool, request: Request, response: Response) -> None:
    s = get_settings()
    token = secrets.token_urlsafe(32)
    lifetime = timedelta(days=s.session_remember_days) if remember else timedelta(hours=s.session_hours)
    db.add(AuthSession(id=_token_hash(token), user_id=user.id, remember=remember,
                       user_agent=request.headers.get("user-agent", "")[:400], ip=client_ip(request),
                       expires_at=utcnow() + lifetime))
    user.last_login_at = utcnow()
    await db.commit()
    set_session_cookie(response, token, remember)


def set_session_cookie(response: Response, token: str, remember: bool) -> None:
    s = get_settings()
    response.set_cookie(
        COOKIE, token, httponly=True, secure=s.cookie_secure, samesite="lax", path="/",
        # No max_age = the browser drops it when it closes: "don't remember this device".
        max_age=s.session_remember_days * 86400 if remember else None,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="lax", secure=get_settings().cookie_secure)


async def session_for_token(db: AsyncSession, token: str | None) -> tuple[AuthSession, User] | None:
    if not token:
        return None
    sess = await db.get(AuthSession, _token_hash(token))
    now = utcnow()
    if not sess or sess.revoked_at or sess.expires_at <= now:
        return None
    user = await db.get(User, sess.user_id)
    if not user:
        return None
    # Sliding expiry, written at most every 5 minutes.
    if now - sess.last_seen_at > timedelta(minutes=5):
        s = get_settings()
        sess.last_seen_at = now
        sess.expires_at = now + (timedelta(days=s.session_remember_days) if sess.remember
                                 else timedelta(hours=s.session_hours))
        await db.commit()
    return sess, user


async def current_session(request: Request, db: AsyncSession = Depends(get_db)) -> tuple[AuthSession, User]:
    found = await session_for_token(db, request.cookies.get(COOKIE))
    if not found:
        raise HTTPException(401, "Sign in to continue")
    request.state.user_id = found[1].id
    return found


async def current_user(found: tuple[AuthSession, User] = Depends(current_session)) -> User:
    return found[1]


async def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Only an admin can change this, because it affects every user.")
    return user


async def websocket_user(ws: WebSocket) -> User | None:
    async with SessionLocal() as db:
        found = await session_for_token(db, ws.cookies.get(COOKIE))
        return found[1] if found else None


async def revoke_session(db: AsyncSession, session_id: str, user_id: UUID) -> bool:
    res = await db.execute(update(AuthSession).where(AuthSession.id == session_id, AuthSession.user_id == user_id,
                                                     AuthSession.revoked_at.is_(None))
                           .values(revoked_at=utcnow()))
    await db.commit()
    return bool(res.rowcount)


async def user_sessions(db: AsyncSession, user_id: UUID) -> list[AuthSession]:
    return list((await db.scalars(select(AuthSession).where(
        AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None), AuthSession.expires_at > utcnow())
        .order_by(AuthSession.last_seen_at.desc()))).all())


# ---------------- brute-force throttle ----------------
class Throttle:
    """At most `limit` failures per key in `window` seconds. In-memory: fine for one API process."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def _trim(self, key: str) -> deque[float]:
        q = self.hits[key]
        while q and q[0] < time.monotonic() - self.window:
            q.popleft()
        return q

    def blocked(self, *keys: str) -> float:
        """Seconds until the most-limited key is allowed again (0 = allowed)."""
        wait = 0.0
        for k in keys:
            q = self._trim(k)
            if len(q) >= self.limit:
                wait = max(wait, q[0] + self.window - time.monotonic())
        return wait

    def fail(self, *keys: str) -> None:
        for k in keys:
            self._trim(k).append(time.monotonic())

    def reset(self, key: str) -> None:
        self.hits.pop(key, None)


login_throttle = Throttle(limit=8, window=15 * 60)
signup_throttle = Throttle(limit=10, window=3600)  # accounts per IP per hour
create_throttle = Throttle(limit=20, window=3600)  # interviews per user per hour (each one costs LLM calls)
analytics_throttle = Throttle(limit=600, window=3600)  # analytics events per IP per hour


def throttled(t: Throttle, key: str, what: str) -> None:
    """Count one use against `key`; 429 once the limit is reached."""
    if wait := t.blocked(key):
        raise HTTPException(429, f"Too many {what}. Try again in {int(wait // 60) + 1} minutes.")
    t.fail(key)


# ---------------- CSRF: Origin check ----------------
class OriginGuard(BaseHTTPMiddleware):
    """Reject state-changing requests that a browser sent from another site. Requests without an Origin header
    (curl, server-to-server) pass; browsers always send Origin on cross-site POST/PUT/PATCH/DELETE."""

    SAFE = {"GET", "HEAD", "OPTIONS"}

    async def dispatch(self, request, call_next):
        if request.method not in self.SAFE:
            origin = request.headers.get("origin")
            allowed = {o.strip() for o in get_settings().cors_origins.split(",") if o.strip()}
            if origin and origin not in allowed:
                return JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)
        return await call_next(request)


class SecurityHeaders(BaseHTTPMiddleware):
    """API responses are data, never pages: forbid sniffing, framing and active content."""

    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        h = resp.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        h.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if request.url.path.startswith("/api/auth"):
            h.setdefault("Cache-Control", "no-store")
        return resp


def origin_allowed(origin: str | None) -> bool:
    """Browsers always send Origin on WebSocket handshakes, so a foreign Origin is a cross-site attempt. No Origin
    means a non-browser client, which can't borrow a victim's cookies."""
    return origin is None or origin in {o.strip() for o in get_settings().cors_origins.split(",") if o.strip()}


# ---------------- signed agent tokens ----------------
def _secret() -> bytes:
    s = get_settings()
    if s.secret_key:
        return s.secret_key.encode()
    f = Path(BACKEND_DIR) / "data" / ".secret_key"
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(secrets.token_urlsafe(48))
        f.chmod(0o600)
    return f.read_text().strip().encode()


def agent_token(user_id: UUID, ttl_s: int = 12 * 3600) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"u": str(user_id), "exp": int(time.time()) + ttl_s}).encode())
    sig = base64.urlsafe_b64encode(hmac.new(_secret(), body, hashlib.sha256).digest())
    return f"{body.decode()}.{sig.decode()}"


def verify_agent_token(token: str | None) -> UUID | None:
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    good = base64.urlsafe_b64encode(hmac.new(_secret(), body.encode(), hashlib.sha256).digest()).decode()
    if not hmac.compare_digest(sig, good):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(body))
        if data["exp"] < time.time():
            return None
        return UUID(data["u"])
    except (ValueError, KeyError):
        return None


def utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()
