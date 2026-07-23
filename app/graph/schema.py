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
        description="Copy-pasteable verification commands (LogQL, docker, curl, …)",
    )
    fix_suggestions: list[str] = Field(
        default_factory=list,
        description="Human remediation steps to resolve this category "
        "(not auto-executed by the agent)",
    )


class Diagnosis(BaseModel):
    # Per-category assessments: one entry per distinct problem in the logs.
    # Optional/defaulted for backward compatibility with older payloads.
    issue_categories: list[CategoryAssessment] = Field(default_factory=list)
    primary_hypothesis: Hypothesis
    secondary_hypotheses: list[Hypothesis] = Field(default_factory=list)
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
