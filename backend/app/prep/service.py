"""The preparation journey: job intake -> candidate context -> preparation plan -> practice interview -> feedback.

Ground rules (from the workbook): facts are never invented. Everything extracted from a posting carries a quote that
is checked against the page; the candidate profile only treats what the user said or approved as `confirmed`; drafts
built from it say what's missing instead of filling gaps. All state lives in Postgres so the journey survives
refreshes and can be resumed.
"""

import asyncio
import logging
import re
import unicodedata
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import llm
from ..agent import planner
from ..agent.memory import new_memory
from ..config import get_settings
from ..db import (Candidate, Interview, Opportunity, PrepPlan, PrepQuestion, PrepSession, ProfileFact,
                  ProfileFactRevision, SessionLocal, User)
from . import fetcher
from .schemas import CoverageResult, JobExtraction, PracticeReview, PreparationPlan, ResumeFacts

log = logging.getLogger(__name__)
_tasks: set[asyncio.Task] = set()


def spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------- grounding ----------------
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
    s = re.sub(r"[*_`#>|\\[\]]", " ", s)  # markdown decoration from the extractor
    return re.sub(r"\s+", " ", s).strip()


def verify(quote: str, source: str) -> bool:
    """Is the quote really in the source text? Tolerates whitespace/markdown/quote-style differences."""
    q, s = _norm(quote), _norm(source)
    if len(q) < 8:
        return False
    if q in s:
        return True
    q = q.rstrip(".;:,")  # quotes often end a sentence early or late
    return len(q) >= 30 and (q[:60] in s or q[-60:] in s)


# ---------------- 1. job intake ----------------
EXTRACT_SYSTEM = """You read a job posting or application page and extract what it actually says.
- Extract only what the page states. Every requirement, responsibility, skill and application question needs an exact
  quote copied character for character from the page. If you can't quote it, leave it out.
- Application questions are questions the application itself asks the candidate (e.g. "Why do you want to work
  here?"). Don't turn requirements into questions, and don't invent any.
- The `interpretation` block is your own reading (seniority, likely interview focus). Keep it separate from facts.
- If the page isn't a job or application page, set is_job_posting=false and say why."""


async def run_intake(prep_id: UUID) -> None:
    async with SessionLocal() as db:
        prep = await db.get(PrepSession, prep_id)
        opp = await db.get(Opportunity, prep.opportunity_id)
        try:
            if opp.source_kind == "url" and not opp.source_text:
                try:
                    page = await fetcher.read_page(opp.source_url)
                except fetcher.FetchRefused as e:
                    page = fetcher.Page("needs_text", reason=str(e))
                opp.fetched_at = now()
                if page.status != "ok":
                    opp.fetch_status, opp.fetch_error = "needs_text", page.reason
                    prep.status = "needs_text"
                    prep.pending_action = "Paste the job description: we couldn't read that page"
                    await db.commit()
                    return
                opp.fetch_status, opp.fetch_method = "ok", page.method
                opp.final_url, opp.source_text, opp.title = page.final_url, page.text[:60000], page.title[:300]
                await db.commit()
            await _extract(db, opp, prep)
            if opp.fetch_status == "ok":
                await _read_application_early(db, prep, opp)  # in the background, alongside the coverage check
            await refresh_coverage(db, prep)
        except Exception as e:
            log.exception("intake failed")
            prep.status, prep.error = "failed", f"{type(e).__name__}: {e}"[:500]
            prep.pending_action = "Try again, or paste the job description"
            await db.commit()


async def _read_application_early(db: AsyncSession, prep: PrepSession, opp: Opportunity) -> None:
    """Read the application form now (known job boards only), so the screening call can cover its questions.
    Reading only: nothing is filled or sent until the candidate asks."""
    from ..apply import service as apply
    from ..db import Application
    ats, _ = apply.apply_url(opp.final_url or opp.source_url)
    if ats == "generic" or await db.scalar(select(Application.id).where(Application.prep_id == prep.id).limit(1)):
        return
    await apply.start(db, prep)


async def _extract(db: AsyncSession, opp: Opportunity, prep: PrepSession) -> None:
    prep.status, prep.pending_action = "intake", "Reading the posting"
    await db.commit()
    ex = await llm.structured(JobExtraction, EXTRACT_SYSTEM,
                              f"SOURCE: {opp.final_url or opp.source_url or 'pasted text'}\n\n"
                              + llm.untrusted("job page", opp.source_text[:25000]), temperature=0, reasoning_effort=get_settings().prep_reasoning_effort)
    if not ex.is_job_posting:
        opp.fetch_status, opp.fetch_error = "needs_text", ex.not_posting_reason or "That doesn't look like a job posting"
        prep.status, prep.pending_action = "needs_text", "Paste the job description: that page wasn't a job posting"
        await db.commit()
        return
    text = opp.source_text

    def items(xs, prefix):
        return [{"id": f"{prefix}{i + 1}", **x.model_dump(), "verified": verify(x.quote, text)} for i, x in enumerate(xs)]

    opp.title = ex.title or opp.title
    opp.company = ex.company
    opp.extraction = {
        "title": ex.title, "company": ex.company, "location": ex.location,
        "requirements": items(ex.requirements, "R"), "responsibilities": items(ex.responsibilities, "D"),
        "skills": items(ex.skills, "S"), "application_questions": items(ex.application_questions, "Q"),
    }
    opp.interpretation = ex.interpretation.model_dump()
    opp.fetch_status = "ok"
    await db.commit()


# ---------------- 2. candidate context ----------------
COVERAGE_SYSTEM = """You compare a job's requirements with what we know about the candidate, then choose the next
few questions to ask them.
- Only CONFIRMED facts count as evidence. A SUGGESTED fact (e.g. drafted from a resume) makes a requirement
  needs_clarification at best: the user hasn't confirmed it.
- supported: a confirmed fact clearly shows it. needs_clarification: related evidence but incomplete or unconfirmed.
  unknown: nothing relevant.
- Never assume or invent anything about the candidate.
- next_questions: up to 3, in this order of importance:
  1. must-have requirements with no evidence, the core technical ones first;
  2. deal-breakers the posting states (location or on-site days, years of experience, required degree or licence,
     work authorization only if the posting asks about it);
  3. must-haves with weak evidence; then nice-to-haves.
- Each question is SHORT (at most 15 words), about ONE thing, and sounds like a friendly recruiter, not a form:
    "What have you built with React recently?"   not   "Could you describe your experience with React, including
    the scale of applications and your specific contributions?"
    "How many years have you worked with Python?" with quick_replies ["Under 2", "2-4 years", "5+ years"].
- Give quick_replies when the answer is naturally one of a few options; leave them empty for open questions.
- Ask about a suggested fact by asking them to confirm or correct it. Don't repeat anything already asked or
  already answered by a confirmed fact. Empty if every must-have is supported or was already asked."""


def fact_view(f: ProfileFact) -> dict:
    return {"id": f.id, "topic": f.topic, "value": f.value, "status": f.status, "source": f.source,
            "updated_at": f.updated_at.isoformat()}


async def user_facts(db: AsyncSession, user_id: UUID) -> list[ProfileFact]:
    return list((await db.scalars(select(ProfileFact).where(ProfileFact.user_id == user_id,
                                                           ProfileFact.status != "retracted")
                                  .order_by(ProfileFact.id))).all())


async def refresh_coverage(db: AsyncSession, prep: PrepSession) -> None:
    opp = await db.get(Opportunity, prep.opportunity_id)
    if opp.fetch_status != "ok":
        return
    reqs = opp.extraction.get("requirements", [])
    facts = await user_facts(db, prep.owner_id)
    asked = (await db.scalars(select(PrepQuestion).where(PrepQuestion.prep_id == prep.id))).all()
    open_q = next((q for q in asked if q.status == "open"), None)
    res = await llm.structured(
        CoverageResult, COVERAGE_SYSTEM,
        "REQUIREMENTS:\n" + "\n".join(f"- {r['id']} ({'must' if r['must_have'] else 'nice'}, {r['category']}): "
                                      f"{r['text']}" for r in reqs)
        + "\n\nALREADY ASKED (don't repeat): " + llm.dumps([{"req": q.requirement_id, "q": q.question,
                                                             "status": q.status} for q in asked
                                                            if q.status != "queued"])
        + "\n\nCANDIDATE FACTS:\n"
        + llm.untrusted("candidate facts", "\n".join(f"- id={f.id} [{f.status.upper()}] {f.topic}: {f.value}"
                                                     for f in facts) or "(none yet)"),
        temperature=0, reasoning_effort=get_settings().prep_reasoning_effort)
    valid_ids = {r["id"] for r in reqs}
    confirmed = {f.id for f in facts if f.status == "confirmed"}
    prep.coverage = {c.requirement_id: {"status": c.status, "note": c.note,
                                        "fact_ids": [i for i in c.fact_ids if i in confirmed]}
                     for c in res.coverage if c.requirement_id in valid_ids}
    for rid, c in prep.coverage.items():  # supported needs at least one confirmed fact behind it
        if c["status"] == "supported" and not c["fact_ids"]:
            c["status"] = "needs_clarification"
    # The first new question goes on screen; the rest wait in a queue so the next one appears instantly after an
    # answer (while coverage is re-checked in the background, which may replace the queue).
    for q in asked:
        if q.status == "queued":
            await db.delete(q)
    has_open = open_q is not None
    seen = {q.question.strip().lower() for q in asked if q.status != "queued"}
    for nq in res.next_questions[:3]:
        if nq.requirement_id not in valid_ids or nq.question.strip().lower() in seen:
            continue
        db.add(PrepQuestion(prep_id=prep.id, requirement_id=nq.requirement_id, question=nq.question.strip(),
                            why=nq.why, quick_replies=[r[:60] for r in nq.quick_replies[:4] if r.strip()],
                            status="queued" if has_open else "open"))
        has_open = True
    if prep.status in ("intake", "context", "needs_text", "failed"):
        prep.status = "context"
    prep.pending_action = ("Answer the next question, or build your plan" if has_open
                           else "Build your preparation plan")
    prep.updated_at = now()
    await db.commit()


async def answer_question(db: AsyncSession, prep: PrepSession, q: PrepQuestion, answer: str) -> ProfileFact:
    opp = await db.get(Opportunity, prep.opportunity_id)
    req = next((r for r in opp.extraction.get("requirements", []) if r["id"] == q.requirement_id), None)
    # The user's own words become a confirmed fact verbatim: no model rewriting of what they said.
    fact = ProfileFact(user_id=prep.owner_id, topic=(req["text"] if req else q.question)[:200], value=answer.strip(),
                       status="confirmed", source="answer", source_ref=f"prep:{prep.id}:q{q.id}")
    db.add(fact)
    await db.flush()
    q.answer, q.status, q.fact_id, q.answered_at = answer.strip(), "answered", fact.id, now()
    await promote_next(db, prep.id)
    await db.commit()
    return fact


async def promote_next(db: AsyncSession, prep_id: UUID) -> None:
    """Show the next queued question right away (the background coverage check may refine the queue later)."""
    nxt = await db.scalar(select(PrepQuestion).where(PrepQuestion.prep_id == prep_id, PrepQuestion.status == "queued")
                          .order_by(PrepQuestion.id).limit(1))
    if nxt:
        nxt.status = "open"


async def edit_fact(db: AsyncSession, fact: ProfileFact, *, value: str | None, status: str | None,
                    topic: str | None, reason: str = "edit") -> None:
    old_v, old_s = fact.value, fact.status
    if value is not None:
        fact.value = value.strip()
    if topic is not None:
        fact.topic = topic.strip()[:200]
    if status is not None:
        fact.status = status
    if (fact.value, fact.status) != (old_v, old_s):
        db.add(ProfileFactRevision(fact_id=fact.id, old_value=old_v, new_value=fact.value, old_status=old_s,
                                   new_status=fact.status, reason=reason))
    fact.updated_at = now()
    await db.commit()


RESUME_SYSTEM = """List the concrete, job-relevant facts a resume states: roles, years, technologies used, projects,
measurable results, education. One fact per item, each with an exact quote from the resume. Don't infer or embellish.
Skip protected characteristics (age, nationality, religion, family, health)."""


async def import_resume(db: AsyncSession, user_id: UUID, text: str) -> int:
    res = await llm.structured(ResumeFacts, RESUME_SYSTEM, llm.untrusted("resume", text[:30000]), temperature=0, reasoning_effort=get_settings().prep_reasoning_effort)
    n = 0
    for f in res.facts[:60]:
        if not verify(f.quote, text):
            continue  # not actually in the resume
        db.add(ProfileFact(user_id=user_id, topic=f.topic[:200], value=f.value, status="suggested", source="resume",
                           source_ref=f.quote[:120]))
        n += 1
    await db.commit()
    return n


# ---------------- 3. preparation plan ----------------
PLAN_SYSTEM = """You write a preparation plan for a practice interview for one specific job and one specific candidate.
- Ground every topic in the posting's requirements (cite requirement ids) and in the candidate's CONFIRMED facts.
  Never invent experience. Where evidence is weak or missing, put it in gaps_to_practice.
- Example questions should be what a real interviewer for this role would ask, phrased naturally and briefly.
- include_coding only if the role writes software.
- answer_drafts: one per application question, first person, built ONLY from confirmed facts (cite fact ids). Where
  a fact is missing write a [bracketed placeholder] and list it under `missing`. Drafts are suggestions for the user
  to edit; never present invented details as true.
- If a revision request is given, apply it while keeping the plan grounded."""


async def generate_plan(db: AsyncSession, prep: PrepSession, request: str = "") -> PrepPlan:
    opp = await db.get(Opportunity, prep.opportunity_id)
    facts = await user_facts(db, prep.owner_id)
    prev = await latest_plan(db, prep.id)
    prep.status, prep.pending_action = "planning", "Building your preparation plan"
    await db.commit()
    ex = opp.extraction
    user = (
        f"ROLE: {ex.get('title')} at {ex.get('company')} ({ex.get('location')})\n"
        f"OUR READING OF THE ROLE (interpretation, not fact): {llm.dumps(opp.interpretation)}\n\n"
        "REQUIREMENTS:\n" + "\n".join(f"- {r['id']} ({'must' if r['must_have'] else 'nice'}): {r['text']}"
                                      for r in ex.get("requirements", []))
        + "\n\nRESPONSIBILITIES:\n" + "\n".join(f"- {r['text']}" for r in ex.get("responsibilities", []))
        + "\n\nAPPLICATION QUESTIONS:\n" + ("\n".join(f"- {q['id']}: {q['question']}"
                                                     for q in ex.get("application_questions", [])) or "(none)")
        + "\n\nCOVERAGE: " + llm.dumps(prep.coverage)
        + "\n\nCANDIDATE FACTS:\n"
        + llm.untrusted("candidate facts", "\n".join(f"- id={f.id} [{f.status.upper()}] {f.topic}: {f.value}"
                                                     for f in facts) or "(none yet)")
        + (f"\n\nPREVIOUS PLAN (version {prev.version}):\n{llm.dumps(prev.plan)}" if prev else "")
        + (f"\n\nREVISION REQUEST FROM THE USER:\n{llm.untrusted('revision request', request)}" if request else "")
    )
    try:
        plan = await llm.structured(PreparationPlan, PLAN_SYSTEM, user, temperature=0.3, reasoning_effort=get_settings().prep_reasoning_effort)
    except Exception:
        prep.status = "context" if not prev else "plan_ready"
        prep.pending_action = "Building the plan failed; try again"
        await db.commit()
        raise
    data = plan.model_dump()
    confirmed = {f.id for f in facts if f.status == "confirmed"}
    for d in data["answer_drafts"]:
        d["fact_ids"] = [i for i in d["fact_ids"] if i in confirmed]
        d["approved"] = False
    version = (prev.version + 1) if prev else 1
    row = PrepPlan(prep_id=prep.id, version=version, plan=data, note=request or ("first plan" if version == 1 else ""))
    db.add(row)
    prep.status, prep.pending_action = "plan_ready", "Start a practice interview"
    prep.updated_at = now()
    await db.commit()
    await db.refresh(row)
    return row


async def latest_plan(db: AsyncSession, prep_id: UUID) -> PrepPlan | None:
    return await db.scalar(select(PrepPlan).where(PrepPlan.prep_id == prep_id).order_by(PrepPlan.version.desc())
                           .limit(1))


# ---------------- 4. practice ----------------
def _focus_text(opp: Opportunity, plan: dict) -> str:
    ex = opp.extraction
    lines = [f"PRACTICE INTERVIEW for {ex.get('title')} at {ex.get('company')}. Follow the PREPARATION PLAN below: ask "
             "about its topics in order of importance, using its example questions as intents.",
             f"Role summary: {opp.interpretation.get('summary', '')}",
             "Requirements: " + "; ".join(r["text"] for r in ex.get("requirements", [])[:20]),
             "PREPARATION PLAN topics:"]
    for t in plan.get("topics", []):
        lines.append(f"- {t['title']} ({t['question_type']}): {t['why']} Examples: {' | '.join(t['example_questions'])}")
    if plan.get("gaps_to_practice"):
        lines.append("Gaps the candidate wants to practise: " + "; ".join(plan["gaps_to_practice"]))
    if not plan.get("include_coding"):
        lines.append("NO CODING ROUND for this role: don't call present_problem; spend that time on role-specific "
                     "and project questions.")
    elif plan.get("coding_focus"):
        lines.append("Coding focus: " + "; ".join(plan["coding_focus"]))
    return "\n".join(lines)[:6000]


async def start_practice(db: AsyncSession, prep: PrepSession, user: User, plan: PrepPlan, duration: int) -> Interview:
    opp = await db.get(Opportunity, prep.opportunity_id)
    facts = [f for f in await user_facts(db, prep.owner_id) if f.status == "confirmed"]
    name = user.display_name or (user.email or "there").split("@")[0]
    profile = {"name": name, "preferred_name": name.split()[0], "headline": user.headline,
               "confirmed_facts": [{"id": f.id, "topic": f.topic, "value": f.value} for f in facts],
               "claims_to_verify": [{"claim": f"{f.topic}: {f.value}", "source": "candidate's own answer"}
                                    for f in facts[:12]],
               "conversation_hooks": [f.value for f in facts[:3]]}
    cand = Candidate(name=name, email=user.email or "", profile=profile, resume_filename="(preparation profile)",
                     resume_text="\n".join(f"{f.topic}: {f.value}" for f in facts))
    db.add(cand)
    await db.flush()
    p = plan.plan
    iv = Interview(owner_id=prep.owner_id, prep_id=prep.id, plan_version=plan.version, candidate_id=cand.id,
                   role=(opp.extraction.get("title") or "Practice interview")[:200], seniority=p.get("seniority", "mid"),
                   duration_min=duration, focus=_focus_text(opp, p), status="created")
    db.add(iv)
    prep.status, prep.pending_action = "practicing", "Finish your practice interview"
    prep.updated_at = now()
    await db.commit()
    spawn(_plan_practice(iv.id))
    return iv


async def _plan_practice(interview_id: UUID) -> None:
    """The existing interview planner, fed the preparation plan (via `focus`) and confirmed facts (via `profile`)."""
    async with SessionLocal() as db:
        iv = await db.get(Interview, interview_id)
        cand = await db.get(Candidate, iv.candidate_id)
        try:
            iv.status = "planning"
            await db.commit()
            plan = await planner.make_plan(db, iv, cand, reasoning_effort=get_settings().prep_reasoning_effort)
            iv.plan = plan.model_dump()
            iv.problem_id = plan.coding_problem_id
            iv.memory = new_memory()
            iv.status = "ready"
            await db.commit()
        except Exception as e:
            log.exception("practice planning failed")
            iv.status, iv.error = "failed", str(e)[:500]
            await db.commit()


# ---------------- 5. feedback ----------------
REVIEW_SYSTEM = """You turn a scored practice interview into feedback the candidate can act on.
- Every strength, gap and improvement must point at something that actually happened: quote the candidate's words or
  cite [turn N]. No generic advice.
- improvements: concrete rewrites ("When asked about X you said '...'; a stronger answer names the scale and your own
  part: ...").
- next_topics: 2-4 topics to practise next, tied to the job's requirement ids and to what went wrong."""


async def review_practice(db: AsyncSession, prep: PrepSession, iv: Interview, turns: list) -> dict:
    opp = await db.get(Opportunity, prep.opportunity_id)
    transcript = "\n".join(f"[turn {t.seq}] {t.speaker.upper()}: {t.text}" for t in turns if t.speaker != "system")
    res = await llm.structured(
        PracticeReview, REVIEW_SYSTEM,
        "REQUIREMENTS:\n" + "\n".join(f"- {r['id']}: {r['text']}" for r in opp.extraction.get("requirements", []))
        + f"\n\nSCORECARD: {llm.dumps({k: v for k, v in (iv.scorecard or {}).items() if k != 'coding'})}\n\nTRANSCRIPT:\n"
        + llm.untrusted("practice interview transcript", transcript[:40000]), temperature=0.2, reasoning_effort=get_settings().prep_reasoning_effort)
    data = {**res.model_dump(), "interview_id": str(iv.id), "plan_version": iv.plan_version,
            "created_at": now().isoformat()}
    recs = dict(prep.recommendations or {})
    recs[str(iv.id)] = data
    prep.recommendations = recs
    if iv.kind != "screening":  # the screening hands over to the technical round, which sets its own status
        prep.status, prep.pending_action = "reviewed", "Practise again, or revise your plan"
    prep.updated_at = now()
    await db.commit()
    return data


async def practice_count(db: AsyncSession, prep_id: UUID) -> int:
    return await db.scalar(select(func.count()).select_from(Interview).where(Interview.prep_id == prep_id)) or 0
