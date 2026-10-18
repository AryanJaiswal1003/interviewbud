"""Screening call: a friendly, recruiter-style conversation (voice, or typed) that fills in the candidate's profile and
the application's answers from what they actually say, then hands over to the technical practice round.

Grounding: every captured answer carries a verbatim quote from the candidate's own turns and is kept only if the quote
is really there (`service.verify` against the transcript). Requirement answers become confirmed profile facts with the
quote attached (the candidate said it; they can correct or remove it afterwards); application answers are stored on
the screening interview and offered to the application preview with their source.

The plan is built from data, not by a model, so the call is ready instantly: items to cover = requirements without
confirmed evidence (must-haves first) + the application form's own questions.
"""

import asyncio
import logging
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import llm
from ..agent.memory import new_memory
from ..apply.fields import classify
from ..config import get_settings
from ..db import (Application, Candidate, Interview, Opportunity, PrepSession, ProfileFact, SessionLocal, Turn,
                  User)
from . import service

log = logging.getLogger(__name__)

MAX_REQUIREMENTS = 6
MAX_APP_QUESTIONS = 8
BUDGETS = [("intro", 1), ("background", 3), ("projects", 4), ("technical", 3), ("coding", 0),
           ("candidate_questions", 1), ("wrap_up", 1)]


class Capture(BaseModel):
    item_id: str = Field(description="Id from the ITEMS list")
    answer: str = Field(description="The answer in the candidate's own terms, first person, one or two sentences; for "
                                    "an item with options, exactly one of the options")
    quote: str = Field(description="Exact words the candidate said that support it, copied character for character "
                                   "from a CANDIDATE line")


class ExtraFact(BaseModel):
    topic: str = Field(description="Short label, e.g. 'Led a payments migration'")
    value: str = Field(description="What they said, first person, close to their wording")
    quote: str = Field(description="Exact words from a CANDIDATE line")


class LiveCapture(BaseModel):
    captures: list[Capture] = Field(description="Only items the latest answer actually answers; usually 0-2")


class FinalCapture(BaseModel):
    captures: list[Capture] = Field(description="Best answer for every item the candidate answered; skip the rest")
    extra_facts: list[ExtraFact] = Field(description="Up to 6 other concrete, job-relevant things they said "
                                                     "(projects, scale, tools, results). Never protected traits.")


CAPTURE_SYSTEM = """You fill in a job application and a candidate profile from a screening conversation.
- Use ONLY what the candidate said. Never infer, embellish or answer for them; if they didn't answer an item, skip it.
- Every capture needs a quote copied exactly from one of the CANDIDATE lines (not the interviewer's).
- For an item with options, the answer must be exactly one of the options, and only if they clearly said so.
- Keep answers short and in their own terms. Skip protected characteristics (age, health, family, religion...)."""


def first_name(user: User) -> str:
    return (user.display_name or (user.email or "there").split("@")[0]).split()[0]


async def application_questions(db: AsyncSession, prep_id: UUID) -> list[dict]:
    """The application form's own questions (once the form has been read), else the posting's stated questions."""
    app = await db.scalar(select(Application).where(Application.prep_id == prep_id)
                          .order_by(Application.created_at.desc()).limit(1))
    out = []
    if app and app.fields:
        for f in app.fields:
            if classify(f) == "question":
                out.append({"id": f"A:{f['key']}", "kind": "application", "ask": f["label"][:300],
                            "options": [o for o in f.get("options") or []][:12], "required": bool(f.get("required"))})
        return out[:MAX_APP_QUESTIONS]
    prep = await db.get(PrepSession, prep_id)
    opp = await db.get(Opportunity, prep.opportunity_id)
    for q in opp.extraction.get("application_questions", []):
        out.append({"id": f"A:{q['id']}", "kind": "application", "ask": q["question"][:300], "options": [],
                    "required": bool(q.get("required"))})
    return out[:MAX_APP_QUESTIONS]


async def items_for(db: AsyncSession, prep: PrepSession) -> list[dict]:
    opp = await db.get(Opportunity, prep.opportunity_id)
    reqs = opp.extraction.get("requirements", [])
    cov = prep.coverage or {}
    gaps = [r for r in reqs if (cov.get(r["id"]) or {}).get("status") != "supported"]
    gaps.sort(key=lambda r: (not r["must_have"], r["category"] not in ("technical", "experience")))
    items = [{"id": r["id"], "kind": "requirement", "ask": r["text"][:300], "options": [],
              "required": r["must_have"]} for r in gaps[:MAX_REQUIREMENTS]]
    return items + await application_questions(db, prep.id)


def plan_for(opp: Opportunity, user: User, items: list[dict], interviewer: str) -> dict:
    ex = opp.extraction
    title, company = ex.get("title") or "this role", ex.get("company") or ""
    at = f" at {company}" if company else ""
    first = first_name(user)
    return {
        "kind": "screening",
        "opening_line": f"Hey {first}, I'm {interviewer}. Thanks for making time! This is a quick, relaxed chat about "
                        f"you and the {title} role{at}. How's your day going so far?",
        "persona_intro": "A relaxed chat of about twelve minutes about their background and projects; their answers "
                         "fill in their application and they can check everything afterwards; then a technical "
                         "practice round.",
        "phase_budgets": [{"phase": p, "minutes": m, "goals": []} for p, m in BUDGETS],
        "items": items,
        "company": company,
    }


async def start(db: AsyncSession, prep: PrepSession, user: User) -> Interview:
    for _ in range(30):  # the form is read right after the posting; give it a moment so its questions are covered
        reading = await db.scalar(select(Application.id).where(Application.prep_id == prep.id,
                                                              Application.status == "reading").limit(1))
        if not reading:
            break
        await asyncio.sleep(1)
        db.expire_all()
    opp = await db.get(Opportunity, prep.opportunity_id)
    facts = [f for f in await service.user_facts(db, prep.owner_id) if f.status == "confirmed"]
    name = user.display_name or (user.email or "there").split("@")[0]
    profile = {"name": name, "preferred_name": first_name(user), "headline": user.headline,
               "confirmed_facts": [{"topic": f.topic, "value": f.value} for f in facts[:20]]}
    cand = Candidate(name=name, email=user.email or "", profile=profile, resume_filename="(screening call)",
                     resume_text="\n".join(f"{f.topic}: {f.value}" for f in facts))
    db.add(cand)
    await db.flush()
    interviewer = (user.preferences or {}).get("interviewer_name") or "Alex"
    plan = plan_for(opp, user, await items_for(db, prep), interviewer)
    memory = new_memory()
    memory["captured"] = {}
    iv = Interview(owner_id=prep.owner_id, prep_id=prep.id, kind="screening", candidate_id=cand.id,
                   role=(opp.extraction.get("title") or "Screening call")[:200], duration_min=13,
                   focus=f"Screening call for {opp.extraction.get('title')} at {opp.extraction.get('company')}",
                   status="ready", plan=plan, memory=memory)
    db.add(iv)
    prep.pending_action = "Finish your screening call"
    prep.updated_at = service.now()
    await db.commit()
    return iv


def items_text(items: list[dict]) -> str:
    lines = []
    for it in items:
        opts = f" OPTIONS: {' | '.join(it['options'])}" if it.get("options") else ""
        lines.append(f"- {it['id']} ({'must' if it.get('required') else 'optional'}): {it['ask']}{opts}")
    return "\n".join(lines)


def _valid(c: Capture, items: dict[str, dict], candidate_text: str) -> bool:
    it = items.get(c.item_id)
    if not it or not c.answer.strip() or not service.verify(c.quote, candidate_text):
        return False
    if it.get("options"):
        match = next((o for o in it["options"] if o.strip().lower() == c.answer.strip().lower()), None)
        if not match:
            return False
        c.answer = match
    return True


async def capture_turn(items: list[dict], captured: dict, question: str, answer: str, dep: str) -> list[dict]:
    """Live, after each candidate turn: which items did that answer cover? (Shown to the candidate as it fills.)"""
    by_id = {it["id"]: it for it in items}
    open_items = [it for it in items if it["id"] not in captured]
    if not open_items or len(answer.split()) < 2:
        return []
    res = await llm.structured(
        LiveCapture, CAPTURE_SYSTEM,
        f"ITEMS STILL OPEN:\n{items_text(open_items)}\n\nLATEST EXCHANGE:\nINTERVIEWER: {question}\n"
        + llm.untrusted("candidate's answer", f"CANDIDATE: {answer}"),
        temperature=0, deployment=dep, reasoning_effort=get_settings().prep_reasoning_effort)
    out = []
    for c in res.captures:
        if c.item_id in captured or not _valid(c, by_id, answer):
            continue
        out.append({"item_id": c.item_id, "answer": c.answer.strip()[:1200], "quote": c.quote.strip()[:400]})
    return out


async def finalize(interview_id: UUID) -> None:
    """After the call: final capture over the whole transcript -> confirmed facts + application answers, then
    refresh coverage, build a new plan version, and set up the technical practice round."""
    async with SessionLocal() as db:
        iv = await db.get(Interview, interview_id)
        if not iv or iv.status == "scored":
            return
        prep = await db.get(PrepSession, iv.prep_id) if iv.prep_id else None
        turns = list((await db.scalars(select(Turn).where(Turn.interview_id == iv.id).order_by(Turn.seq))).all())
        cand_text = "\n".join(t.text for t in turns if t.speaker == "candidate")
        items = iv.plan.get("items", [])
        by_id = {it["id"]: it for it in items}
        try:
            captured: dict = {}
            facts_added: list[int] = []
            if cand_text.strip():
                transcript = "\n".join(f"{'CANDIDATE' if t.speaker == 'candidate' else 'INTERVIEWER'}: {t.text}"
                                       for t in turns if t.speaker != "system")
                res = await llm.structured(
                    FinalCapture, CAPTURE_SYSTEM,
                    f"ITEMS:\n{items_text(items)}\n\n"
                    + llm.untrusted("screening call transcript", transcript[:40000]),
                    temperature=0, reasoning_effort=get_settings().prep_reasoning_effort)
                live = (iv.memory or {}).get("captured", {})
                for c in res.captures:
                    if _valid(c, by_id, cand_text):
                        captured[c.item_id] = {"answer": c.answer.strip()[:1200], "quote": c.quote.strip()[:400]}
                for k, v in live.items():  # the live pass may have caught something the final pass skipped
                    captured.setdefault(k, v)
                if prep:
                    for item_id, c in captured.items():
                        it = by_id[item_id]
                        if it["kind"] != "requirement":
                            continue
                        f = ProfileFact(user_id=prep.owner_id, topic=it["ask"][:200], value=c["answer"],
                                        status="confirmed", source="screening",
                                        source_ref=f"screening:{iv.id}:{c['quote'][:70]}")
                        db.add(f)
                        await db.flush()
                        facts_added.append(f.id)
                    for x in res.extra_facts[:6]:
                        if not service.verify(x.quote, cand_text):
                            continue
                        f = ProfileFact(user_id=prep.owner_id, topic=x.topic[:200], value=x.value.strip()[:1200],
                                        status="confirmed", source="screening",
                                        source_ref=f"screening:{iv.id}:{x.quote[:70]}")
                        db.add(f)
                        await db.flush()
                        facts_added.append(f.id)
            iv.scorecard = {"kind": "screening", "captured": captured, "facts_added": facts_added,
                            "items_total": len(items)}
            iv.status = "scored"
            await db.commit()
        except Exception as e:
            log.exception("screening capture failed")
            await db.rollback()
            iv = await db.get(Interview, interview_id)
            iv.error = f"screening: {e}"[:500]
            await db.commit()
            return
        if not prep:
            return
        prep_id = prep.id
    await _next_round(prep_id, interview_id)


async def _next_round(prep_id: UUID, screening_id: UUID) -> None:
    """Hand over to the technical round: re-check coverage with the new facts, new plan version, practice interview.
    The application's proposed answers are refreshed from the call too (unless the candidate already approved)."""
    from ..apply import service as apply
    async with SessionLocal() as db:
        app = await db.scalar(select(Application).where(Application.prep_id == prep_id)
                              .order_by(Application.created_at.desc()).limit(1))
        if app and app.status == "ready" and app.fields:
            try:
                await apply.map_values(db, app)
            except Exception:
                log.exception("refreshing the application answers failed")
                await db.rollback()
    async with SessionLocal() as db:
        prep = await db.get(PrepSession, prep_id)
        try:
            await service.refresh_coverage(db, prep)
            plan = await service.generate_plan(db, prep, "Updated after the screening call")
            user = await db.get(User, prep.owner_id)
            nxt = await service.start_practice(db, prep, user, plan, 20)
            sc = await db.get(Interview, screening_id)
            sc.scorecard = {**sc.scorecard, "next_interview_id": str(nxt.id)}
            await db.commit()
        except Exception as e:
            log.exception("handover to the technical round failed")
            await db.rollback()
            sc = await db.get(Interview, screening_id)
            sc.scorecard = {**(sc.scorecard or {}), "next_error": f"{type(e).__name__}: {e}"[:300]}
            await db.commit()


async def screening_answers(db: AsyncSession, prep_id: UUID) -> dict[str, dict]:
    """Application answers captured in this preparation's screening calls, newest call first wins."""
    rows = (await db.scalars(select(Interview).where(Interview.prep_id == prep_id, Interview.kind == "screening",
                                                     Interview.status == "scored")
                             .order_by(Interview.created_at))).all()
    out: dict[str, dict] = {}
    for iv in rows:
        items = {it["id"]: it for it in iv.plan.get("items", [])}
        for item_id, c in (iv.scorecard or {}).get("captured", {}).items():
            if item_id.startswith("A:"):
                out[item_id] = {**c, "question": items.get(item_id, {}).get("ask", ""), "interview_id": str(iv.id)}
    return out
