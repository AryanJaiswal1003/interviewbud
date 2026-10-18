"""ACE-style evolving playbook (arXiv 2510.04618): itemised lessons with helpful/harmful counters, merged by
deterministic code. Lessons start `proposed` and only reach the interviewer once a reviewer approves them or
real feedback supports them, because ACE degrades without a reliable feedback signal."""

from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import PlaybookLesson

DUP_SIMILARITY = 0.55  # pg_trgm similarity above which two lessons are considered the same
MAX_ACTIVE_PER_SCOPE = 15


async def active_lessons(db: AsyncSession, scope: str | None, limit: int = 6) -> list[PlaybookLesson]:
    q = select(PlaybookLesson).where(PlaybookLesson.status == "active")
    if scope:
        q = q.where(PlaybookLesson.scope.in_([scope, "global"]))
    q = q.order_by(
        (PlaybookLesson.helpful - PlaybookLesson.harmful).desc(), PlaybookLesson.updated_at.desc()
    ).limit(limit)
    return list((await db.scalars(q)).all())


async def curate(db: AsyncSession, lessons: list[dict], interview_id) -> dict:
    added, merged = 0, 0
    for item in lessons:
        lesson = item["lesson"].strip()
        if len(lesson) < 12:
            continue
        row = (
            await db.execute(
                text(
                    "SELECT id, similarity(lesson, :l) AS s FROM playbook_lessons "
                    "WHERE scope = :scope AND status <> 'rejected' ORDER BY s DESC LIMIT 1"
                ),
                {"l": lesson, "scope": item["scope"]},
            )
        ).first()
        if row and row.s >= DUP_SIMILARITY:
            existing = await db.get(PlaybookLesson, row.id)
            existing.helpful += 1  # re-discovered independently: counts as support
            existing.evidence = (existing.evidence + "\n---\n" + item.get("evidence", ""))[-4000:]
            existing.updated_at = datetime.now(timezone.utc)
            # No auto-activation: lessons change the interviewer for every user, so an admin approves them
            # (repeat discoveries just add support). Otherwise one person running two interviews could plant one.
            merged += 1
        else:
            db.add(
                PlaybookLesson(
                    scope=item["scope"], lesson=lesson, evidence=item.get("evidence", ""),
                    source_interview_id=interview_id,
                )
            )
            added += 1
    await db.flush()
    await _enforce_caps(db)
    await db.commit()
    return {"added": added, "merged": merged}


async def record_usage(db: AsyncSession, usage: list[dict]) -> None:
    for u in usage:
        lesson = await db.get(PlaybookLesson, u["lesson_id"])
        if not lesson or u["verdict"] == "unused":
            continue
        if u["verdict"] == "helpful":
            lesson.helpful += 1
        else:
            lesson.harmful += 1
        if lesson.status == "active" and lesson.harmful >= lesson.helpful + 3:
            lesson.status = "proposed"  # demote: needs a human to look again
        lesson.updated_at = datetime.now(timezone.utc)
    await db.commit()


async def _enforce_caps(db: AsyncSession) -> None:
    scopes = (await db.scalars(select(PlaybookLesson.scope).distinct())).all()
    for scope in scopes:
        n = await db.scalar(
            select(func.count()).where(PlaybookLesson.scope == scope, PlaybookLesson.status == "active")
        )
        if n and n > MAX_ACTIVE_PER_SCOPE:
            worst = (
                await db.scalars(
                    select(PlaybookLesson)
                    .where(PlaybookLesson.scope == scope, PlaybookLesson.status == "active")
                    .order_by((PlaybookLesson.helpful - PlaybookLesson.harmful).asc())
                    .limit(n - MAX_ACTIVE_PER_SCOPE)
                )
            ).all()
            for w in worst:
                w.status = "proposed"
