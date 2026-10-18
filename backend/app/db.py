import os
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.pool import NullPool

from .config import get_settings

# DB_NULLPOOL=1 (tests): open a connection per use, since asyncpg connections are bound to the event loop that
# made them and the WebSocket test client runs its own loop.
engine = (create_async_engine(get_settings().database_url, poolclass=NullPool) if os.getenv("DB_NULLPOOL") == "1"
          else create_async_engine(get_settings().database_url, pool_size=10, max_overflow=10))
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSONB, list: JSONB}


class User(Base):
    __tablename__ = "users"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # No sign-up or sign-in: an account is a name plus a random account key that lives in the person's browser
    # (only its SHA-256 is stored here). Email/password are legacy, kept only for accounts made before.
    email: Mapped[str | None] = mapped_column(String(320), unique=True, index=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_key_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    headline: Mapped[str] = mapped_column(String(200), default="")
    accent: Mapped[str] = mapped_column(String(16), default="amber")  # avatar colour when there's no photo
    avatar: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    avatar_type: Mapped[str] = mapped_column(String(40), default="")
    timezone: Mapped[str] = mapped_column(String(64), default="")
    # interviewer_name, default_role, default_seniority, default_duration, default_voice, theme, analytics
    preferences: Mapped[dict] = mapped_column(default=dict)
    # Admins change things that affect everyone: interviewer lessons, the voice catalogue. First account = admin.
    is_admin: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthSession(Base):
    """One signed-in device. The cookie holds a random token; only its SHA-256 is stored here."""
    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256(token) hex
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    remember: Mapped[bool] = mapped_column(default=False)
    user_agent: Mapped[str] = mapped_column(String(400), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    anon_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    name: Mapped[str] = mapped_column(String(80), index=True)
    path: Mapped[str] = mapped_column(String(300), default="")
    props: Mapped[dict] = mapped_column(default=dict)
    user_agent: Mapped[str] = mapped_column(String(400), default="")


# ---------------- preparation journey: job -> candidate context -> plan -> practice -> feedback ----------------
class Opportunity(Base):
    """A job or application page and what was extracted from it. `extraction` items carry a verbatim quote and a
    `verified` flag (the quote was found in `source_text`); `interpretation` holds model-generated reading."""
    __tablename__ = "opportunities"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source_url: Mapped[str] = mapped_column(Text, default="")
    source_kind: Mapped[str] = mapped_column(String(10), default="url")  # url | text
    fetch_method: Mapped[str] = mapped_column(String(20), default="")  # http | firecrawl | browserless | pasted
    fetch_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | ok | needs_text | failed
    fetch_error: Mapped[str] = mapped_column(Text, default="")
    final_url: Mapped[str] = mapped_column(Text, default="")
    source_text: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str] = mapped_column(String(300), default="")
    company: Mapped[str] = mapped_column(String(200), default="")
    extraction: Mapped[dict] = mapped_column(default=dict)
    interpretation: Mapped[dict] = mapped_column(default=dict)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProfileFact(Base):
    """One thing we know about the user, shared across preparation sessions. `confirmed` = the user said or approved
    it; `suggested` = drafted from a resume or inferred, not to be relied on until the user confirms it."""
    __tablename__ = "profile_facts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    topic: Mapped[str] = mapped_column(String(200))
    value: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="confirmed")  # confirmed | suggested | retracted
    source: Mapped[str] = mapped_column(String(20), default="answer")  # answer | resume | manual
    source_ref: Mapped[str] = mapped_column(String(120), default="")  # e.g. prep question id, resume quote
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProfileFactRevision(Base):
    __tablename__ = "profile_fact_revisions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fact_id: Mapped[int] = mapped_column(ForeignKey("profile_facts.id", ondelete="CASCADE"), index=True)
    old_value: Mapped[str] = mapped_column(Text, default="")
    new_value: Mapped[str] = mapped_column(Text, default="")
    old_status: Mapped[str] = mapped_column(String(12), default="")
    new_status: Mapped[str] = mapped_column(String(12), default="")
    reason: Mapped[str] = mapped_column(String(40), default="edit")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PrepSession(Base):
    """One preparation journey for one opportunity. `status` is the workflow step; `pending_action` is what the
    user is expected to do next, so a refreshed page resumes exactly there."""
    __tablename__ = "prep_sessions"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    opportunity_id: Mapped[UUID] = mapped_column(ForeignKey("opportunities.id", ondelete="CASCADE"))
    # intake | needs_text | context | planning | plan_ready | practicing | reviewed | failed
    status: Mapped[str] = mapped_column(String(20), default="intake")
    pending_action: Mapped[str] = mapped_column(String(300), default="")
    coverage: Mapped[dict] = mapped_column(default=dict)  # {requirement_id: {status, fact_ids, note}}
    recommendations: Mapped[dict] = mapped_column(default=dict)  # {interview_id: {...}}
    similar_jobs: Mapped[dict] = mapped_column(default=dict)  # {generated_at, items: [...]} (see prep/jobs.py)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PrepQuestion(Base):
    """A follow-up question the agent asked about a requirement, and the user's answer."""
    __tablename__ = "prep_questions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    prep_id: Mapped[UUID] = mapped_column(ForeignKey("prep_sessions.id", ondelete="CASCADE"), index=True)
    requirement_id: Mapped[str] = mapped_column(String(20), default="")
    question: Mapped[str] = mapped_column(Text)
    why: Mapped[str] = mapped_column(Text, default="")
    quick_replies: Mapped[list] = mapped_column(default=list)  # tap-to-answer options, when the answer is one of a few
    answer: Mapped[str] = mapped_column(Text, default="")
    # queued (asked next) | open (on screen) | answered | skipped
    status: Mapped[str] = mapped_column(String(12), default="open")
    fact_id: Mapped[int | None] = mapped_column(ForeignKey("profile_facts.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PrepPlan(Base):
    """A versioned preparation plan, separate from any live interview so it can be inspected, revised and reused."""
    __tablename__ = "prep_plans"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    prep_id: Mapped[UUID] = mapped_column(ForeignKey("prep_sessions.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    plan: Mapped[dict] = mapped_column(default=dict)
    note: Mapped[str] = mapped_column(Text, default="")  # what changed / the revision request
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Application(Base):
    """Applying to the job on the candidate's behalf. Every step is separate and explicit: read the form ->
    the candidate reviews and approves each value (with where it came from) -> fill (screenshot, not sent) ->
    submit only on the candidate's click. `history` is an append-only log of what happened."""
    __tablename__ = "applications"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    prep_id: Mapped[UUID] = mapped_column(ForeignKey("prep_sessions.id", ondelete="CASCADE"), index=True)
    apply_url: Mapped[str] = mapped_column(Text, default="")
    ats: Mapped[str] = mapped_column(String(20), default="")  # greenhouse | lever | ashby | generic
    # reading | ready | approved | filling | filled | submitting | submitted | needs_you | failed
    status: Mapped[str] = mapped_column(String(20), default="reading")
    fields: Mapped[list] = mapped_column(default=list)  # the form as read: [{key, label, type, required, ...}]
    values: Mapped[dict] = mapped_column(default=dict)  # {key: {value, source, detail, state}}
    approved: Mapped[dict] = mapped_column(default=dict)  # snapshot of values at approval
    fill_report: Mapped[dict] = mapped_column(default=dict)
    confirmation: Mapped[str] = mapped_column(Text, default="")
    history: Mapped[list] = mapped_column(default=list)
    error: Mapped[str] = mapped_column(Text, default="")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Voice(Base):
    __tablename__ = "voices"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200))
    fish_model_id: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Candidate(Base):
    __tablename__ = "candidates"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str] = mapped_column(String(320), default="")
    resume_filename: Mapped[str] = mapped_column(String(400), default="")
    resume_text: Mapped[str] = mapped_column(Text, default="")
    profile: Mapped[dict] = mapped_column(default=dict)
    # Elo-style ability estimate for coding problems.
    theta: Mapped[float] = mapped_column(Float, default=1500.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Interview(Base):
    __tablename__ = "interviews"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    # Set when this is a practice interview from a preparation journey (and which plan version it used).
    prep_id: Mapped[UUID | None] = mapped_column(ForeignKey("prep_sessions.id", ondelete="SET NULL"), nullable=True,
                                                 index=True)
    plan_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # interview (technical practice / resume screen) | screening (friendly background call that fills the profile
    # and the application answers)
    kind: Mapped[str] = mapped_column(String(12), default="interview")
    candidate_id: Mapped[UUID] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(200))
    seniority: Mapped[str] = mapped_column(String(50), default="mid")
    duration_min: Mapped[int] = mapped_column(Integer, default=45)
    focus: Mapped[str] = mapped_column(Text, default="")
    voice_id: Mapped[str] = mapped_column(String(64), default="")
    # created | planning | ready | live | completed | scored | failed
    status: Mapped[str] = mapped_column(String(20), default="created")
    plan: Mapped[dict] = mapped_column(default=dict)
    memory: Mapped[dict] = mapped_column(default=dict)
    scorecard: Mapped[dict] = mapped_column(default=dict)
    critique: Mapped[dict] = mapped_column(default=dict)
    reviewer_overrides: Mapped[dict] = mapped_column(default=dict)
    problem_id: Mapped[int | None] = mapped_column(ForeignKey("problems.id"), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    candidate: Mapped[Candidate] = relationship(lazy="joined")


class Turn(Base):
    __tablename__ = "turns"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    interview_id: Mapped[UUID] = mapped_column(ForeignKey("interviews.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[str] = mapped_column(String(20))  # interviewer | candidate | system
    text: Mapped[str] = mapped_column(Text)
    phase: Mapped[str] = mapped_column(String(40), default="")
    channel: Mapped[str] = mapped_column(String(10), default="voice")  # voice | text
    metrics: Mapped[dict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Problem(Base):
    __tablename__ = "problems"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # LeetCode question_id
    slug: Mapped[str] = mapped_column(String(200), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    difficulty: Mapped[str] = mapped_column(String(10), index=True)
    tags: Mapped[list] = mapped_column(default=list)
    description: Mapped[str] = mapped_column(Text)
    constraints: Mapped[list] = mapped_column(default=list)
    examples: Mapped[list] = mapped_column(default=list)
    starter_code: Mapped[str] = mapped_column(Text)
    preamble: Mapped[str] = mapped_column(Text)
    entry_point: Mapped[str] = mapped_column(String(200))
    reference_solution: Mapped[str] = mapped_column(Text)
    estimated_date: Mapped[str] = mapped_column(String(10), default="")
    split: Mapped[str] = mapped_column(String(10), default="train")
    elo: Mapped[float] = mapped_column(Float, default=1500.0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class ProblemTest(Base):
    __tablename__ = "problem_tests"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id", ondelete="CASCADE"), index=True)
    # sample (visible) | hidden | hard | injected (agent-added, per interview)
    kind: Mapped[str] = mapped_column(String(12))
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    call_args: Mapped[str] = mapped_column(Text)  # e.g. "nums = [3,3], target = 6"
    assertion: Mapped[str] = mapped_column(Text)  # executable python assert using `candidate`
    expected_repr: Mapped[str] = mapped_column(Text, default="")
    # {"v": expected value as plain JSON} or {"err": why it couldn't be computed}; computed outside any run that
    # includes candidate code (see coding/service.py). NULL = not computed yet.
    expected_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    interview_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), nullable=True, index=True
    )
    rationale: Mapped[str] = mapped_column(Text, default="")


class CodeRun(Base):
    __tablename__ = "code_runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    interview_id: Mapped[UUID] = mapped_column(ForeignKey("interviews.id", ondelete="CASCADE"), index=True)
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id"))
    language: Mapped[str] = mapped_column(String(20), default="python")
    code: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(10))  # run (samples) | submit (all)
    summary: Mapped[dict] = mapped_column(default=dict)
    results: Mapped[list] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkspaceSnapshot(Base):
    __tablename__ = "workspace_snapshots"
    interview_id: Mapped[UUID] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), primary_key=True
    )
    code: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    whiteboard: Mapped[list] = mapped_column(default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlaybookLesson(Base):
    __tablename__ = "playbook_lessons"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # global | intro | background | projects | technical | coding | candidate_questions | wrap_up
    scope: Mapped[str] = mapped_column(String(40), index=True)
    lesson: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default="proposed")  # proposed | active | rejected
    helpful: Mapped[int] = mapped_column(Integer, default=0)
    harmful: Mapped[int] = mapped_column(Integer, default=0)
    source_interview_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("interviews.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    interview_id: Mapped[UUID] = mapped_column(ForeignKey("interviews.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(20))  # candidate | reviewer
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.create_all)
        # create_all doesn't alter existing tables: add ownership to interviews created before accounts existed.
        await conn.execute(text("ALTER TABLE interviews ADD COLUMN IF NOT EXISTS owner_id UUID "
                                "REFERENCES users(id) ON DELETE CASCADE"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_interviews_owner_id ON interviews (owner_id)"))
        await conn.execute(text("ALTER TABLE problem_tests ADD COLUMN IF NOT EXISTS expected_json JSONB"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT false"))
        await conn.execute(text("ALTER TABLE users ALTER COLUMN email DROP NOT NULL"))
        await conn.execute(text("ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS account_key_hash VARCHAR(64)"))
        await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_account_key_hash ON users "
                                "(account_key_hash)"))
        await conn.execute(text("ALTER TABLE interviews ADD COLUMN IF NOT EXISTS prep_id UUID "
                                "REFERENCES prep_sessions(id) ON DELETE SET NULL"))
        await conn.execute(text("ALTER TABLE interviews ADD COLUMN IF NOT EXISTS plan_version INTEGER"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_interviews_prep_id ON interviews (prep_id)"))
        await conn.execute(text("ALTER TABLE interviews ADD COLUMN IF NOT EXISTS kind VARCHAR(12) NOT NULL "
                                "DEFAULT 'interview'"))
        await conn.execute(text("ALTER TABLE prep_sessions ADD COLUMN IF NOT EXISTS similar_jobs JSONB NOT NULL "
                                "DEFAULT '{}'::jsonb"))
        await conn.execute(text("ALTER TABLE prep_questions ADD COLUMN IF NOT EXISTS quick_replies JSONB NOT NULL "
                                "DEFAULT '[]'::jsonb"))
        # Existing installs: if nobody is admin yet, the earliest account becomes admin.
        await conn.execute(text("UPDATE users SET is_admin = true WHERE id = (SELECT id FROM users ORDER BY created_at "
                                "LIMIT 1) AND NOT EXISTS (SELECT 1 FROM users WHERE is_admin)"))


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
