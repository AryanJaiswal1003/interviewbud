"""Structured-output schemas (strict JSON schema compatible: every field required, no free-form dicts)."""

from typing import Literal

from pydantic import BaseModel, Field

Phase = Literal["intro", "background", "projects", "technical", "coding", "candidate_questions", "wrap_up"]
PHASES: list[str] = ["intro", "background", "projects", "technical", "coding", "candidate_questions", "wrap_up"]


# ---------- Resume ----------
class Role(BaseModel):
    company: str
    title: str
    start: str
    end: str
    highlights: list[str]
    technologies: list[str]


class Project(BaseModel):
    name: str
    context: str = Field(description="Where/why: company, side project, research, course")
    summary: str
    technologies: list[str]
    claimed_impact: list[str] = Field(description="Quantified claims verbatim from the resume")
    candidate_role: str = Field(description="What the resume says the candidate personally did")


class Education(BaseModel):
    institution: str
    degree: str
    field: str
    end: str


class Claim(BaseModel):
    claim: str = Field(description="A specific, checkable claim (numbers, ownership, scale, novel tech)")
    source: str = Field(description="Which role/project it comes from")
    probe: str = Field(description="A question that would verify it")


class CandidateProfile(BaseModel):
    name: str
    preferred_name: str = Field(description="First name to address them by")
    email: str
    headline: str = Field(description="One-line professional summary")
    years_experience: float
    seniority_estimate: Literal["intern", "junior", "mid", "senior", "staff"]
    primary_languages: list[str]
    skills: list[str]
    domains: list[str]
    work: list[Role]
    projects: list[Project]
    education: list[Education]
    claims_to_verify: list[Claim]
    conversation_hooks: list[str] = Field(
        description="Genuine, job-relevant details useful for rapport (a notable project, an interesting "
        "transition, an open-source contribution). Never protected characteristics."
    )


# ---------- Plan ----------
class ProbeLadder(BaseModel):
    project_name: str
    why_this_project: str
    opening_question: str
    probes: list[str] = Field(description="Ordered: ownership, architecture, trade-offs, numbers, one layer "
                              "down (x2), failure/debugging, results, reflection")
    claims_to_check: list[str]
    signals_sought: list[str]


class TechnicalTopic(BaseModel):
    topic: str
    tied_to: str = Field(description="Which resume item makes this relevant")
    question: str
    follow_ups: list[str]
    strong_answer_looks_like: str


class PhaseBudget(BaseModel):
    phase: Phase
    minutes: float
    goals: list[str]


class InterviewPlan(BaseModel):
    persona_intro: str = Field(description="How the interviewer introduces themself and the format, spoken")
    opening_line: str = Field(description="First thing said: warm, specific to this candidate, natural speech")
    background_questions: list[str]
    projects: list[ProbeLadder]
    technical_topics: list[TechnicalTopic]
    coding_problem_id: int
    coding_framing: str = Field(description="1-2 spoken sentences connecting the problem to their work")
    coding_focus: list[str] = Field(description="What to watch for in this problem (edge cases, complexity)")
    phase_budgets: list[PhaseBudget]
    closing_notes: str


# ---------- Memory extraction (per candidate turn, background) ----------
class FactUpdate(BaseModel):
    key: str
    value: str


class ThreadUpdate(BaseModel):
    topic: str
    why: str
    priority: Literal["low", "medium", "high"]
    status: Literal["open", "resolved"]


class ClaimUpdate(BaseModel):
    claim: str
    status: Literal["unverified", "consistent", "inconsistent"]
    evidence: str


class EvidenceItem(BaseModel):
    dimension: Literal[
        "communication", "technical_depth", "ownership_impact", "problem_solving", "coding", "verification"
    ]
    polarity: Literal["positive", "negative", "neutral"]
    quote: str = Field(description="Short verbatim quote from the candidate")
    note: str


class MemoryDelta(BaseModel):
    facts: list[FactUpdate]
    threads: list[ThreadUpdate]
    claims: list[ClaimUpdate]
    evidence: list[EvidenceItem]
    rapport_note: str = Field(description="Pace/anxiety/style cue worth remembering, or empty string")
    candidate_sentiment: Literal["comfortable", "neutral", "anxious", "frustrated"]


# ---------- Scorecard ----------
class DimensionScore(BaseModel):
    dimension: Literal[
        "communication", "technical_depth", "ownership_impact", "problem_solving", "coding", "verification"
    ]
    score: int = Field(ge=0, le=4, description="1-4 anchored; 0 = insufficient signal")
    confidence: Literal["low", "medium", "high"]
    rationale: str
    evidence: list[str] = Field(description="Verbatim candidate quotes or concrete observations, with [turn N]")


class Scorecard(BaseModel):
    summary: str
    recommendation: Literal["strong_hire", "hire", "no_hire", "strong_no_hire"]
    dimensions: list[DimensionScore]
    strengths: list[str]
    concerns: list[str]
    claims_verified: list[str]
    claims_unverified_or_inconsistent: list[str]
    suggested_next_round_focus: list[str]


# ---------- Self-critique ----------
class Lesson(BaseModel):
    scope: Literal["global", "intro", "background", "projects", "technical", "coding", "candidate_questions",
                   "wrap_up"]
    lesson: str = Field(description="Imperative, specific, reusable instruction for future interviews")
    evidence: str = Field(description="What happened in this interview that motivates it, with [turn N]")


class LessonUsage(BaseModel):
    lesson_id: int
    verdict: Literal["helpful", "harmful", "unused"]


class Critique(BaseModel):
    overall: str
    timing: str
    missed_probes: list[str]
    leading_questions: list[str]
    guardrail_issues: list[str]
    hinting: str
    candidate_experience: str
    new_lessons: list[Lesson]
    lesson_usage: list[LessonUsage]
