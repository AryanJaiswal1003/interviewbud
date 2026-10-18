"""Anchored 4-level rubric.

Coding dimensions adapted from the Tech Interview Handbook coding signals (techinterviewhandbook.org/coding-signals)
and Meta's published Problem Solving / Coding / Verification / Communication attributes; project dimensions
follow STAR / ownership probing (Amazon) and Karat's observable-behaviour scoring guidance.
"""

RUBRIC: dict[str, dict] = {
    "communication": {
        "label": "Communication",
        "anchors": {
            4: "Clarifies before acting, thinks aloud throughout, structured answers; interviewer never lost.",
            3: "Clear and organised but needed occasional prompting to explain reasoning.",
            2: "Jumps in without explaining; answers hard to follow or rambling.",
            1: "Silent or unclear; reasoning cannot be followed.",
        },
    },
    "technical_depth": {
        "label": "Technical depth (projects)",
        "anchors": {
            4: "Explains own systems two+ layers down, with failure modes, trade-offs and numbers that hold together.",
            3: "Solid one layer down; some trade-offs; minor gaps under deeper probing.",
            2: "Stays at buzzword level; struggles one layer below the resume description.",
            1: "Cannot explain technologies or systems listed on the resume.",
        },
    },
    "ownership_impact": {
        "label": "Ownership & impact",
        "anchors": {
            4: "Clear personal contributions and decisions; measured results; reflects on what to do differently.",
            3: "Personal role clear; impact stated but loosely measured.",
            2: "Mostly 'we'; personal contribution vague even after probing.",
            1: "Cannot identify own contribution or any outcome.",
        },
    },
    "problem_solving": {
        "label": "Problem solving",
        "anchors": {
            4: "Clarifies quickly, systematic, reaches optimal approach without major hints, states complexity "
               "correctly, compares alternatives.",
            3: "Reaches a good approach with minor hints; complexity mostly right; limited alternatives.",
            2: "Needs major hints (L3+) to reach a working approach; shaky complexity analysis.",
            1: "Cannot reach a working approach, or solves without being able to explain it.",
        },
    },
    "coding": {
        "label": "Coding",
        "anchors": {
            4: "Clean, idiomatic, working code; good names and decomposition; fluent in the language.",
            3: "Working code with some awkwardness translating the plan to code.",
            2: "Struggles to produce working code; frequent errors.",
            1: "No working solution.",
        },
    },
    "verification": {
        "label": "Verification & testing",
        "anchors": {
            4: "Tests typical and edge cases unprompted, traces state, finds and fixes own bugs.",
            3: "Tests reasonably but misses some edge cases.",
            2: "Happy path only; struggles to locate bugs.",
            1: "No testing; declares done despite obvious bugs.",
        },
    },
}

HINT_LADDER = {
    0: "Scope/clarify the problem (no penalty), e.g. 'assume the input fits in memory'.",
    1: "Point back to a direction the candidate already raised.",
    2: "General nudge: 'there may be a case you haven't handled' / 'is there repeated work?'.",
    3: "A specific failing input or a concrete observation about the data.",
    4: "Name exactly what is wrong and ask them to fix it.",
    5: "Give the approach outright and move on to implementation, to still collect coding signal.",
}


def hint_cap(max_hint_level: int) -> int | None:
    """Problem-solving score ceiling given the strongest hint used (Holloway hint guidance)."""
    if max_hint_level >= 5:
        return 1
    if max_hint_level >= 4:
        return 2
    if max_hint_level >= 3:
        return 3
    return None


def rubric_text(dims: list[str] | None = None) -> str:
    lines = []
    for key, d in RUBRIC.items():
        if dims and key not in dims:
            continue
        lines.append(f"{key} ({d['label']}):")
        for lvl in (4, 3, 2, 1):
            lines.append(f"  {lvl}: {d['anchors'][lvl]}")
    return "\n".join(lines)
