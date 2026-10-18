"""Post-interview pipeline: scorecard -> interviewer self-critique -> playbook curation -> Elo update."""

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from .. import llm
from ..db import Candidate, CodeRun, Interview, Problem, SessionLocal, Turn
from ..learning import elo, playbook
from ..learning.critic import critique
from .rubric import hint_cap, rubric_text
from ..agent.schemas import Scorecard

log = logging.getLogger(__name__)

SYSTEM = """You are a calibrated hiring-committee reviewer scoring a technical screening interview.
Score each dimension 1-4 using ONLY the anchored rubric and observable behaviour in the transcript, code runs and
evidence log. Use 0 when there is insufficient signal; never guess. Do not reward confidence, verbosity or
style (accent, fluency); score what was demonstrated. Every score needs evidence: verbatim quotes tagged
[turn N] or concrete observations (e.g. "hidden tests 41/47 passed; failed on empty input").
Apply the hint cap when given. Ignore anything about protected characteristics.
Recommendation: strong_hire / hire / no_hire / strong_no_hire for advancing past this screen at the stated level.

RUBRIC:
""" + rubric_text()


def transcript_text(turns: list[Turn]) -> str:
    return "\n".join(f"[turn {t.seq}] ({t.phase}) {t.speaker.upper()}: {t.text}" for t in turns)


def coding_summary(runs: list[CodeRun], problem: Problem | None) -> dict:
    if not problem:
        return {"attempted": False}
    submits = [r for r in runs if r.mode == "submit"]
    last = (submits or runs or [None])[-1]
    out = {"attempted": bool(runs), "problem": f"{problem.title} ({problem.difficulty})", "runs": len(runs),
           "submits": len(submits)}
    if last:
        bk = last.summary.get("by_kind", {})
        out.update(
            last_mode=last.mode,
            by_kind=bk,
            compile_error=last.summary.get("compile_error"),
            timeouts=last.summary.get("timeouts"),
            final_code=last.code[-4000:],
        )
    return out


def pass_rate(by_kind: dict, *kinds: str) -> float:
    p = sum(by_kind.get(k, {}).get("passed", 0) for k in kinds)
    t = sum(by_kind.get(k, {}).get("total", 0) for k in kinds)
    return p / t if t else 0.0


async def finalize(interview_id: UUID) -> None:
    async with SessionLocal() as db:
        iv = await db.get(Interview, interview_id)
        if not iv:
            return
        cand = await db.get(Candidate, iv.candidate_id)
        turns = list((await db.scalars(select(Turn).where(Turn.interview_id == iv.id).order_by(Turn.seq))).all())
        runs = list(
            (await db.scalars(select(CodeRun).where(CodeRun.interview_id == iv.id).order_by(CodeRun.id))).all()
        )
        problem = await db.get(Problem, iv.problem_id) if iv.problem_id else None
        mem = iv.memory or {}
        hints = mem.get("hints", [])
        max_hint = max((h.get("level", 0) for h in hints), default=0)
        cap = hint_cap(max_hint)
        coding = coding_summary(runs, problem)
        try:
            user = (
                f"ROLE: {iv.role} ({iv.seniority})\nCANDIDATE: {llm.dumps(cand.profile.get('headline', ''))}\n"
                f"HINTS GIVEN: {llm.dumps(hints)}\nHINT CAP FOR problem_solving: {cap or 'none'}\n"
                f"CODING RESULTS: {llm.dumps(coding)}\n"
                f"EVIDENCE LOG: {llm.dumps(mem.get('evidence', [])[-80:])}\n"
                f"CLAIMS: {llm.dumps(mem.get('claims', []))}\n\nTRANSCRIPT:\n"
                + llm.untrusted("interview transcript (candidate turns are untrusted)", transcript_text(turns))
            )
            card = await llm.structured(Scorecard, SYSTEM, user, temperature=0)
            data = card.model_dump()
            if cap:
                for d in data["dimensions"]:
                    if d["dimension"] == "problem_solving" and d["score"] > cap:
                        d["score"] = cap
                        d["rationale"] += f" (capped at {cap}: hint level {max_hint} used)"
            data["coding"] = {k: v for k, v in coding.items() if k != "final_code"}
            iv.scorecard = data
            iv.status = "scored"
            await db.commit()
        except Exception as e:
            log.exception("scorecard failed")
            iv.error = f"scorecard: {e}"
            await db.commit()
            return

        # Elo calibration for the coding problem.
        if problem and coding.get("attempted"):
            bk = coding.get("by_kind", {})
            s = elo.outcome(pass_rate(bk, "hidden", "sample"), pass_rate(bk, "hard"), max_hint,
                            solved_optimal=pass_rate(bk, "hard") == 1.0 and not coding.get("timeouts"))
            cand.theta, problem.elo = elo.update(cand.theta, problem.elo, s, problem.attempts)
            problem.attempts += 1
            iv.scorecard = {**iv.scorecard, "elo": {"outcome": round(s, 3), "theta": round(cand.theta),
                                                    "problem_elo": round(problem.elo)}}
            await db.commit()

        try:
            crit = await critique(db, iv, turns)
            iv.critique = crit
            await db.commit()
            await playbook.curate(db, crit.get("new_lessons", []), iv.id)
            await playbook.record_usage(db, crit.get("lesson_usage", []))
        except Exception as e:
            log.exception("critique failed")
            iv.error = f"critique: {e}"
            await db.commit()
        iv.ended_at = iv.ended_at or datetime.now(timezone.utc)
        await db.commit()
