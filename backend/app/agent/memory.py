"""Working memory (MemGPT-style core blocks) kept in-process during a live interview and persisted as JSONB.

Updated off the latency-critical path by a cheap extractor after each candidate turn."""

import time

from .. import llm
from .schemas import MemoryDelta

MAX_EVIDENCE = 200


def new_memory() -> dict:
    return {
        "phase": "intro",
        "phase_started": time.time(),
        "phase_history": [],
        "facts": {},
        "threads": [],
        "claims": [],
        "evidence": [],
        "hints": [],
        "rapport": [],
        "sentiment": "neutral",
        "covered_projects": [],
        "problem_presented": False,
        "lessons_used": [],
    }


def apply_delta(mem: dict, delta: MemoryDelta, turn_seq: int) -> None:
    for f in delta.facts:
        if f.key and f.value:
            mem["facts"][f.key[:60]] = f.value[:300]
    threads = {t["topic"].lower(): t for t in mem["threads"]}
    for t in delta.threads:
        key = t.topic.lower()
        if key in threads:
            threads[key].update(status=t.status, priority=t.priority)
        else:
            threads[key] = {**t.model_dump(), "turn": turn_seq}
    mem["threads"] = [t for t in threads.values()][-25:]
    claims = {c["claim"].lower(): c for c in mem["claims"]}
    for c in delta.claims:
        claims[c.claim.lower()] = {**c.model_dump(), "turn": turn_seq}
    mem["claims"] = list(claims.values())[-30:]
    for e in delta.evidence:
        mem["evidence"].append({**e.model_dump(), "turn": turn_seq, "phase": mem["phase"]})
    mem["evidence"] = mem["evidence"][-MAX_EVIDENCE:]
    if delta.rapport_note:
        mem["rapport"] = (mem["rapport"] + [delta.rapport_note])[-6:]
    mem["sentiment"] = delta.candidate_sentiment


def render(mem: dict) -> str:
    open_threads = [t for t in mem["threads"] if t.get("status") == "open"]
    open_threads.sort(key=lambda t: {"high": 0, "medium": 1, "low": 2}.get(t.get("priority"), 3))
    lines = ["KNOWN FACTS: " + ("; ".join(f"{k}: {v}" for k, v in list(mem["facts"].items())[-25:]) or "—")]
    if open_threads:
        lines.append("OPEN THREADS (follow up when natural):")
        lines += [f"  - [{t['priority']}] {t['topic']} — {t['why']}" for t in open_threads[:8]]
    pending = [c for c in mem["claims"] if c.get("status") != "consistent"]
    if pending:
        lines.append("CLAIMS STATUS:")
        lines += [f"  - ({c['status']}) {c['claim']}" for c in pending[:8]]
    if mem["hints"]:
        lines.append("HINTS GIVEN: " + "; ".join(f"L{h['level']} @{h['minute']}m" for h in mem["hints"]))
    if mem["rapport"]:
        lines.append("RAPPORT NOTES: " + " | ".join(mem["rapport"]))
    lines.append(f"CANDIDATE SEEMS: {mem['sentiment']}")
    by_dim: dict[str, int] = {}
    for e in mem["evidence"]:
        by_dim[e["dimension"]] = by_dim.get(e["dimension"], 0) + 1
    lines.append("EVIDENCE COLLECTED PER DIMENSION: " + (", ".join(f"{k}={v}" for k, v in by_dim.items()) or "none"))
    return "\n".join(lines)


EXTRACT_SYSTEM = """You maintain an interviewer's working memory. Given the latest exchange and current memory,
return ONLY new or changed items:
- facts: job-relevant facts the candidate stated (stack, scale numbers, team size, their role). Never protected
  characteristics (age, family, religion, nationality, health, etc.).
- threads: things worth following up later (vague answer, interesting detail, unexplained number, 'we' without
  'I'); mark threads resolved when the latest answer covers them.
- claims: update resume claims the candidate just supported (consistent) or contradicted (inconsistent).
- evidence: rubric-relevant observations with a SHORT verbatim quote.
Empty lists are fine. Be terse."""


async def extract(mem: dict, interviewer_text: str, candidate_text: str, deployment: str) -> MemoryDelta:
    user = (
        f"PHASE: {mem['phase']}\nCURRENT MEMORY:\n{render(mem)}\n\n"
        f"INTERVIEWER: {interviewer_text}\nCANDIDATE: " + llm.untrusted("candidate's last turn", candidate_text)
    )
    return await llm.structured(MemoryDelta, EXTRACT_SYSTEM, user, deployment=deployment, temperature=0)
