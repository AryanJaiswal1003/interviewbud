"""Structured outputs for the preparation journey. Every item that claims to come from the posting carries a verbatim
quote; the server checks the quote against the page text and marks the item `verified` (see service.verify)."""

from typing import Literal

from pydantic import BaseModel, Field


class Requirement(BaseModel):
    text: str = Field(description="The requirement, concise, in the posting's own terms")
    quote: str = Field(description="Exact excerpt from the posting (copied character for character) that states it")
    must_have: bool = Field(description="True if the posting presents it as required; false if 'nice to have'/bonus")
    category: Literal["technical", "experience", "domain", "education", "soft_skill", "logistics"]


class Sourced(BaseModel):
    text: str
    quote: str = Field(description="Exact excerpt from the posting that states it")


class ApplicationQuestion(BaseModel):
    question: str
    quote: str = Field(description="Exact excerpt from the page where the question appears")
    required: bool


class Interpretation(BaseModel):
    """The model's reading of the posting: useful, but not stated facts."""
    summary: str = Field(description="Two sentences: what this role is and what success in it looks like")
    seniority_guess: Literal["intern", "junior", "mid", "senior", "staff", "unclear"]
    likely_interview_focus: list[str] = Field(description="3-6 areas an interviewer for this role would probe")
    technical_role: bool = Field(description="True if the role writes software, so a coding exercise makes sense")


class JobExtraction(BaseModel):
    is_job_posting: bool = Field(description="False if the text isn't a job or application page")
    not_posting_reason: str = ""
    title: str
    company: str
    location: str
    requirements: list[Requirement] = Field(description="Every qualification the posting lists, required and preferred")
    responsibilities: list[Sourced]
    skills: list[Sourced] = Field(description="Named technologies, tools and methods")
    application_questions: list[ApplicationQuestion] = Field(
        description="Questions the application asks the candidate to answer (essay or short-answer questions on the "
        "page). Empty if none are shown. Never invent them.")
    interpretation: Interpretation


class RequirementCoverage(BaseModel):
    requirement_id: str
    status: Literal["supported", "needs_clarification", "unknown"]
    fact_ids: list[int] = Field(description="Ids of CONFIRMED facts that support it; empty unless supported")
    note: str = Field(description="One short sentence: what's known or missing")


class NextQuestion(BaseModel):
    requirement_id: str
    question: str = Field(description="One short, friendly question; asks for facts, never assumes them")
    why: str = Field(description="One short sentence the user sees: why this matters for the role")


class CoverageResult(BaseModel):
    coverage: list[RequirementCoverage]
    next_question: NextQuestion | None = Field(
        description="The single most useful question to ask next, or null if nothing important is missing")


class ResumeFact(BaseModel):
    topic: str = Field(description="Short label, e.g. 'Python experience', 'Led a migration'")
    value: str = Field(description="What the resume says, close to its wording")
    quote: str = Field(description="Exact excerpt from the resume")


class ResumeFacts(BaseModel):
    facts: list[ResumeFact]


class PlanTopic(BaseModel):
    title: str
    why: str = Field(description="Why this topic, tied to the posting")
    requirement_ids: list[str]
    question_type: Literal["behavioral", "project_deep_dive", "technical_concepts", "coding", "system_design",
                           "role_specific"]
    example_questions: list[str] = Field(description="2-3 questions an interviewer would ask for this role")
    candidate_angle: str = Field(description="How the candidate's CONFIRMED facts relate (or 'no evidence yet')")


class PlanSkill(BaseModel):
    skill: str
    requirement_ids: list[str]
    how_assessed: str


class AnswerDraft(BaseModel):
    question_id: str
    draft: str = Field(description="First-person draft built ONLY from confirmed facts; [brackets] where facts are "
                                   "missing")
    fact_ids: list[int]
    missing: list[str] = Field(description="Facts the user should add to finish this answer")


class PreparationPlan(BaseModel):
    summary: str
    topics: list[PlanTopic] = Field(description="4-7 topics, most important first")
    skills_to_assess: list[PlanSkill]
    question_types: list[str]
    include_coding: bool
    coding_focus: list[str]
    seniority: Literal["intern", "junior", "mid", "senior", "staff"]
    gaps_to_practice: list[str] = Field(description="Requirements with weak or no evidence the user should prepare")
    answer_drafts: list[AnswerDraft] = Field(description="One per application question; empty if there are none")


class NextTopic(BaseModel):
    topic: str
    why: str = Field(description="Cites what happened in the interview (quote or turn)")
    requirement_ids: list[str]


class PracticeReview(BaseModel):
    strengths: list[str] = Field(description="Each cites the candidate's actual words or a turn number")
    gaps: list[str] = Field(description="Each cites the candidate's actual words or a turn number")
    improvements: list[str] = Field(
        description="Concrete rewrites: 'When asked X, you said Y; a stronger answer would ...'")
    next_topics: list[NextTopic]
