"""First-party product analytics, cookie-based and consent-gated.

Cookies:
  ib_consent  "all" | "essential"  set by the browser's cookie banner (readable by JS), 1 year
  ib_aid      anonymous device id  set here, HttpOnly, 1 year, only once ib_consent=all
Events are stored in Postgres (analytics_events); nothing is sent to a third party.
"""

import re
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import auth
from ..config import get_settings
from ..db import AnalyticsEvent, User, get_db, utcnow

router = APIRouter(prefix="/api/analytics", tags=["analytics"])
AID = "ib_aid"
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,60}$")


class EventIn(BaseModel):
    name: str
    path: str = Field("", max_length=300)
    props: dict = {}


class BatchIn(BaseModel):
    events: list[EventIn] = Field(default_factory=list, max_length=50)


@router.post("/events")
async def collect(body: BatchIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    if request.cookies.get("ib_consent") != "all":
        return {"stored": 0, "reason": "no analytics consent"}
    ip = f"ip:{auth.client_ip(request)}"
    if auth.analytics_throttle.blocked(ip):
        return {"stored": 0, "reason": "rate limited"}
    for _ in body.events:
        auth.analytics_throttle.fail(ip)
    aid = request.cookies.get(AID) or ""
    if not re.fullmatch(r"[A-Za-z0-9_\-]{16,64}", aid):
        aid = secrets.token_urlsafe(18)
        response.set_cookie(AID, aid, max_age=365 * 86400, httponly=True, samesite="lax", path="/",
                            secure=get_settings().cookie_secure)
    found = await auth.session_for_token(db, request.cookies.get(auth.COOKIE))
    user_id = found[1].id if found else None
    ua = request.headers.get("user-agent", "")[:400]
    n = 0
    for e in body.events:
        if not NAME_RE.match(e.name):
            continue
        props = {k: v for k, v in list(e.props.items())[:20] if isinstance(v, (str, int, float, bool)) and len(str(v)) < 300}
        db.add(AnalyticsEvent(user_id=user_id, anon_id=aid, name=e.name, path=e.path, props=props, user_agent=ua))
        n += 1
    await db.commit()
    return {"stored": n}


@router.get("/summary")
async def summary(days: int = 30, user: User = Depends(auth.current_user), db: AsyncSession = Depends(get_db)):
    """Event counts and active devices over the last `days` days."""
    since = utcnow() - timedelta(days=max(1, min(365, days)))
    by_name = dict((await db.execute(select(AnalyticsEvent.name, func.count()).where(AnalyticsEvent.at >= since)
                                     .group_by(AnalyticsEvent.name).order_by(func.count().desc()))).all())
    daily = (await db.execute(select(func.date_trunc("day", AnalyticsEvent.at).label("d"),
                                     func.count(func.distinct(AnalyticsEvent.anon_id)))
                              .where(AnalyticsEvent.at >= since).group_by("d").order_by("d"))).all()
    return {"since": auth.utc(since), "events": by_name,
            "daily_active_devices": [{"day": d.date().isoformat(), "devices": n} for d, n in daily]}
