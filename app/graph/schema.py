"""Structured output schema for the correlate node."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Hypothesis(BaseModel):
    cause: str
    confidence: int = Field(ge=0, le=100)
    evidence: str = ""


class Diagnosis(BaseModel):
    primary_hypothesis: Hypothesis
    secondary_hypotheses: list[Hypothesis] = Field(default_factory=list)
    blast_radius_assessment: str
    suggested_next_steps: list[str]
    confidence_note: Literal["low", "medium", "high"]
