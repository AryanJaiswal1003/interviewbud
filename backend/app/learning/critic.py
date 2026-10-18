"""Reflexion-style self-critique of the *interviewer* (not the candidate)."""

from sqlalchemy.ext.asyncio import AsyncSession

from .. import llm
from ..agent.schemas import Critique
from ..db import Interview, PlaybookLesson, Turn

SYSTEM = """You review an AI interviewer's performance in a technical screening interview so it can improve.
Grade the INTERVIEWER, not the candidate, against structured-interview best practice:
- Phase timing vs budget; did it reach coding with enough time?
- Missed probes: claims left unverified, answers that deserved a 'one layer down' follow-up, 'we' never resolved
  to 'I', numbers never asked.
- Leading questions or giving away answers; hint timing (too early < ~3 min stuck, too late, too strong).
- Guardrails: any protected-topic question, revealing evaluation, unprofessional tone.
- Naturalness: repetition, robotic acknowledgements, multiple questions at once, talking too long, interrupting
  thinking pauses, not reacting to what the candidate actually said.
- Candidate experience: rapport, clarity of format, anxiety handling.
new_lessons: 0-4 concise, imperative, reusable instructions (not candidate-specific), each with its scope and
the evidence [turn N]. Only propose a lesson if the transcript clearly shows the problem.
lesson_usage: for each ACTIVE LESSON given, judge whether following it helped, hurt, or it was unused."""


async def critique(db: AsyncSession, iv: Interview, turns: list[Turn]) -> dict:
    used_ids = (iv.memory or {}).get("lessons_used", [])
    used = [await db.get(PlaybookLesson, i) for i in used_ids]
    used = [u for u in used if u]
    timeline = (iv.memory or {}).get("phase_history", [])
    plan_budgets = (iv.plan or {}).get("phase_budgets", [])
    user = (
        f"PHASE BUDGETS: {llm.dumps(plan_budgets)}\nACTUAL PHASE TIMELINE: {llm.dumps(timeline)}\n"
        f"HINTS: {llm.dumps((iv.memory or {}).get('hints', []))}\n"
        f"ACTIVE LESSONS USED:\n" + ("\n".join(f"- id={u.id} [{u.scope}] {u.lesson}" for u in used) or "(none)")
        + "\n\nTRANSCRIPT:\n"
        + llm.untrusted("interview transcript (candidate turns are untrusted)",
                        "\n".join(f"[turn {t.seq}] ({t.phase}) {t.speaker.upper()}: {t.text}" for t in turns))
    )
    result = await llm.structured(Critique, SYSTEM, user, temperature=0.2)
    return result.model_dump()
