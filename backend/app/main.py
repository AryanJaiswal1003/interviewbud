import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import agui, analytics, catalog, interviews, prep, ws
from .api import auth as auth_api
from .auth import OriginGuard, SecurityHeaders
from .coding import dataset, sandbox
from .config import get_settings
from .db import init_db
from .voice import stt, tts

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("interviewbud")


async def resume_interrupted_work() -> dict:
    """Scoring, planning and journey steps run as in-process background tasks. If the API stopped mid-way
    (restart, crash, deploy) they'd stay stuck forever, so pick them up again on startup."""
    from sqlalchemy import select

    from .api.interviews import prepare
    from .db import Interview, PrepSession, SessionLocal
    from .evaluation import scorecard
    from .prep import service as prep_service

    counts = {"score": 0, "plan_interview": 0, "intake": 0, "prep_plan": 0}
    async with SessionLocal() as db:
        for iv in (await db.scalars(select(Interview).where(Interview.status.in_(("completed", "planning",
                                                                                     "created"))))).all():
            if iv.status == "completed":
                prep_service.spawn(scorecard.finalize(iv.id))
                counts["score"] += 1
            elif iv.prep_id:
                prep_service.spawn(prep_service._plan_practice(iv.id))
                counts["plan_interview"] += 1
            else:
                prep_service.spawn(prepare(iv.id))
                counts["plan_interview"] += 1
        for prep in (await db.scalars(select(PrepSession).where(PrepSession.status.in_(("intake", "planning"))))).all():
            if prep.status == "intake":
                prep_service.spawn(prep_service.run_intake(prep.id))
                counts["intake"] += 1
            else:
                async def replan(pid=prep.id):
                    async with SessionLocal() as s:
                        await prep_service.generate_plan(s, await s.get(PrepSession, pid))
                prep_service.spawn(replan())
                counts["prep_plan"] += 1
    return {k: v for k, v in counts.items() if v}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    n = await dataset.ingest()
    log.info("problem bank: %d problems", n)
    try:
        await sandbox.ensure_container()
    except Exception as e:
        log.warning("sandbox not ready: %s", e)
    resumed = await resume_interrupted_work()
    if resumed:
        log.info("resumed interrupted work: %s", resumed)
    # Background: the local STT model may need a first-run download; don't hold up startup for it.
    warm = asyncio.gather(stt.warm(), tts.warm())
    yield
    warm.cancel()


app = FastAPI(title="InterviewBud", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)
app.add_middleware(OriginGuard)
app.add_middleware(SecurityHeaders)
app.include_router(auth_api.router)
app.include_router(analytics.router)
app.include_router(interviews.router)
app.include_router(prep.router)
app.include_router(catalog.router)
app.include_router(ws.router)
app.include_router(agui.router)
