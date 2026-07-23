"""Structured output schema for the correlate node."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Hypothesis(BaseModel):
    cause: str
    confidence: int = Field(ge=0, le=100)
    evidence: str = ""


class CategoryAssessment(BaseModel):
    """One logical group of related failures found in the logs.

    Logs frequently contain several unrelated problems at once; each distinct
    problem gets its own category with an independent assessment.
    """

    category: str = Field(
        description="Short logical label, e.g. database, cache, search, "
        "jvm-memory, gateway, auth, external-api, host"
    )
    cause: str
    confidence: int = Field(ge=0, le=100)
    evidence: str = ""
    suggested_next_step: str = Field(
        default="",
        description="Single best short investigative action for this category",
    )
    tool_run_examples: list[str] = Field(
        default_factory=list,
        description="Required for each category: 1–3 copy-pasteable verification "
        "commands specific to this category (LogQL, docker, curl, …)",
        min_length=0,
    )
    fix_suggestions: list[str] = Field(
        default_factory=list,
        description="Required for each category: 1–3 human remediation steps for "
        "this category (not auto-executed by the agent)",
    )


class Diagnosis(BaseModel):
    # Per-category assessments: one entry per distinct problem in the logs.
    # Optional/defaulted for backward compatibility with older payloads.
    issue_categories: list[CategoryAssessment] = Field(
        default_factory=list,
        description="SOURCE OF TRUTH: one full assessment per distinct problem "
        "(evidence + tool_run_examples + fix_suggestions). Do not put problems "
        "only in secondary_hypotheses.",
    )
    primary_hypothesis: Hypothesis
    secondary_hypotheses: list[Hypothesis] = Field(
        default_factory=list,
        description="Short mirror of non-primary category causes only — not a "
        "substitute for full issue_categories entries",
    )
    blast_radius_assessment: str
    suggested_next_steps: list[str]
    tool_run_examples: list[str] = Field(
        default_factory=list,
        description="Top-level copy-pasteable verification commands for the incident",
    )
    fix_suggestions: list[str] = Field(
        default_factory=list,
        description="Top-level human remediation steps (not auto-executed)",
    )
    confidence_note: Literal["low", "medium", "high"]
