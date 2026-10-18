"""Build a personalised interview plan from the profile, role and the real problem bank."""

import random

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import llm
from ..db import Candidate, Interview, Problem
from ..evaluation.rubric import rubric_text
from ..learning import playbook
from .schemas import InterviewPlan

SENIORITY_TARGET = {"intern": 1200, "junior": 1320, "mid": 1500, "senior": 1640, "staff": 1720}

# Phase skeleton per total duration (minutes). Sums match the duration; coding gets the largest block.
# Based on Meta's 45-min screen (5 intro / 35 coding / 5 questions) extended with project deep-dives.
BUDGETS = {
    30: {"intro": 2, "background": 3, "projects": 7, "technical": 0, "coding": 14, "candidate_questions": 3,
         "wrap_up": 1},
    45: {"intro": 2, "background": 4, "projects": 10, "technical": 5, "coding": 18, "candidate_questions": 4,
         "wrap_up": 2},
    60: {"intro": 3, "background": 5, "projects": 13, "technical": 8, "coding": 23, "candidate_questions": 6,
         "wrap_up": 2},
}


def budgets_for(duration: int) -> dict[str, float]:
    key = min(BUDGETS, key=lambda d: abs(d - duration))
    scale = duration / key
    return {k: round(v * scale, 1) for k, v in BUDGETS[key].items()}


async def shortlist(db: AsyncSession, candidate: Candidate, seniority: str, n: int = 24) -> list[Problem]:
    seen = select(Interview.problem_id).where(
        Interview.candidate_id == candidate.id, Interview.problem_id.is_not(None)
    )
    target = SENIORITY_TARGET.get(seniority, 1500)
    q = (
        select(Problem)
        .where(
            Problem.entry_point.like("Solution().%"),
            func.length(Problem.description) < 2600,
            Problem.id.not_in(seen),
            func.abs(Problem.elo - target) <= 220,
        )
        # Recent problems (the dataset's 2024-25 test split) are least likely to be memorised.
        .order_by(Problem.split.desc(), Problem.estimated_date.desc())
        .limit(400)
    )
    pool = list((await db.scalars(q)).all())
    rnd = random.Random(str(candidate.id))
    recent, older = pool[:120], pool[120:]
    rnd.shuffle(recent)
    rnd.shuffle(older)
    picked, tags_used = [], set()
    for p in recent + older:
        key = tuple(p.tags[:2])
        if key in tags_used:
            continue
        tags_used.add(key)
        picked.append(p)
        if len(picked) >= n:
            break
    return picked


SYSTEM = """You are the lead interviewer preparing a personalised technical screening interview.
You will receive the candidate profile, the role, optional job description, the time budget, lessons learned
from past interviews, and a shortlist of real coding problems. Produce the plan.

Principles (structured interviewing with adaptive follow-ups):
- Same skeleton for every candidate (intro -> background -> projects -> technical -> coding ->
  candidate_questions -> wrap_up); personalise the content.
- opening_line: warm, natural, spoken; greet them by preferred name and mention one specific, genuine detail
  from their resume. At most 25 words, natural speech ("Hey Maya, thanks for jumping on. Oh, I was reading about
  your Kafka Streams migration, really curious about that. How's your day going?").
- persona_intro: the facts for a short spoken format explanation, at most 40 words: you're an AI interviewer,
  roughly thirty minutes, background then their projects then a bit of coding, take thinking time whenever,
  questions at the end.
- Background questions: past-behaviour, job-relevant, open-ended. Never touch protected topics.
- Write every question the way a relaxed human interviewer actually says it out loud: short (usually under 15
  words), casual, ONE question, no reciting their resume back to them, no "specifically", no "walk me through X,
  Y and Z", no "rather than", no compound "and what..." second half.
  (Examples are from a different candidate, a mobile engineer; copy the style, never the words.)
    AI-ish: "You've worked on iOS at Lumo and then Fieldnote. What drew you to mobile specifically, and what's kept
            you there rather than moving to backend?"
    Human:  "How'd you get into mobile in the first place?"
    AI-ish: "Walk me through a recent crash spike you owned: what broke, what you changed, and how you knew it stuck."
    Human:  "Tell me about the last really bad bug you chased down."
    AI-ish: "Let's start with the offline sync rewrite. You said you led it and cut sync conflicts by 80 percent...
            Walk me through what you personally built versus the team."
    Human:  "Okay, the offline sync rewrite. What part of that was yours?"
  The follow-up detail (numbers, trade-offs, ownership) goes in probes, asked one at a time later.
- Pick the 2 most technically rich projects/roles (most claims, most relevant to the role). Probe ladder per project:
  ownership ("what did you personally build?"), architecture, trade-offs, numbers, one layer down (x2),
  failure/debugging, results, reflection. Seed probes with their actual claims.
- technical_topics: 2-3 deeper questions inside their stack (e.g. how a technology they used works internally,
  scaling a system they built), each tied to a resume item, with a description of a strong answer.
- coding_problem_id must be one id from the shortlist. Choose the problem that best fits the role and seniority
  and is solvable in the coding budget. coding_framing: one spoken sentence, at most 25 words, linking it to their
  work without changing the problem's semantics.
- phase_budgets must use the given minutes.
- All spoken text: natural conversational English, contractions, short, the way a person talks on a call, no
  markdown, no lists."""


async def make_plan(db: AsyncSession, interview: Interview, candidate: Candidate,
                    reasoning_effort: str = "") -> InterviewPlan:
    probs = await shortlist(db, candidate, interview.seniority)
    if not probs:
        raise RuntimeError("Problem bank is empty; run the dataset ingest")
    budgets = budgets_for(interview.duration_min)
    lessons = await playbook.active_lessons(db, scope=None, limit=12)
    lines = [
        f"- id={p.id} | {p.title} | {p.difficulty} | elo={p.elo:.0f} | tags={', '.join(p.tags)} | "
        f"{p.description[:180].replace(chr(10), ' ')}"
        for p in probs
    ]
    user = (
        f"ROLE: {interview.role} (target seniority: {interview.seniority})\n"
        f"DURATION: {interview.duration_min} min; budgets (minutes): {llm.dumps(budgets)}\n"
        f"INTERVIEWER FOCUS / JOB DESCRIPTION:\n{interview.focus or '(none provided)'}\n\n"
        f"CANDIDATE PROFILE (extracted from their resume):\n{llm.untrusted('resume profile', llm.dumps(candidate.profile))}\n\n"
        f"LESSONS FROM PAST INTERVIEWS (apply them):\n"
        + ("\n".join(f"- [{l.scope}] {l.lesson}" for l in lessons) or "(none yet)")
        + f"\n\nRUBRIC:\n{rubric_text()}\n\nCODING SHORTLIST:\n" + "\n".join(lines)
    )
    plan = await llm.structured(InterviewPlan, SYSTEM, user, temperature=0.4, reasoning_effort=reasoning_effort)
    ids = {p.id for p in probs}
    if plan.coding_problem_id not in ids:
        plan.coding_problem_id = probs[0].id
    return plan
