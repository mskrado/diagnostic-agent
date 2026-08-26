"""Structured output schema for the correlate node."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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


_CONFIDENCE_NOTES = frozenset({"low", "medium", "high"})


def repair_diagnosis_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Fill gaps Nova Micro often leaves in partial ToolUse / JSON payloads.

    Promotes ``issue_categories`` / ``secondary_hypotheses`` into a missing
    ``primary_hypothesis`` and applies safe defaults for other required fields
    so a truncated structured response can still render in the UI.
    """
    out = dict(data)

    if not out.get("primary_hypothesis"):
        primary: dict[str, Any] | None = None
        categories = out.get("issue_categories") or []
        if isinstance(categories, list) and categories:
            best = None
            best_conf = -1
            for item in categories:
                if not isinstance(item, dict):
                    continue
                conf = item.get("confidence", 0)
                try:
                    conf_i = int(conf)
                except (TypeError, ValueError):
                    conf_i = 0
                if conf_i >= best_conf and item.get("cause"):
                    best = item
                    best_conf = conf_i
            if best is not None:
                primary = {
                    "cause": str(best.get("cause") or "unknown"),
                    "confidence": max(0, min(100, best_conf if best_conf >= 0 else 0)),
                    "evidence": str(best.get("evidence") or ""),
                }
        if primary is None:
            secondaries = out.get("secondary_hypotheses") or []
            if isinstance(secondaries, list) and secondaries:
                first = secondaries[0]
                if isinstance(first, dict) and first.get("cause"):
                    try:
                        conf_i = int(first.get("confidence") or 0)
                    except (TypeError, ValueError):
                        conf_i = 0
                    primary = {
                        "cause": str(first.get("cause")),
                        "confidence": max(0, min(100, conf_i)),
                        "evidence": str(first.get("evidence") or ""),
                    }
        if primary is None:
            primary = {
                "cause": "incomplete model output",
                "confidence": 0,
                "evidence": "Structured response omitted primary_hypothesis",
            }
        out["primary_hypothesis"] = primary

    if not out.get("blast_radius_assessment"):
        out["blast_radius_assessment"] = "none identified"

    if out.get("suggested_next_steps") is None:
        steps: list[str] = []
        for item in out.get("issue_categories") or []:
            if isinstance(item, dict):
                step = (item.get("suggested_next_step") or "").strip()
                if step:
                    steps.append(step)
        out["suggested_next_steps"] = steps

    note = out.get("confidence_note")
    if isinstance(note, str):
        normalized = note.strip().lower()
        out["confidence_note"] = normalized if normalized in _CONFIDENCE_NOTES else "low"
    elif note is None:
        primary = out.get("primary_hypothesis") or {}
        try:
            conf = int(primary.get("confidence") or 0) if isinstance(primary, dict) else 0
        except (TypeError, ValueError):
            conf = 0
        if conf >= 75:
            out["confidence_note"] = "high"
        elif conf >= 40:
            out["confidence_note"] = "medium"
        else:
            out["confidence_note"] = "low"

    return out


class Diagnosis(BaseModel):
    # Per-category assessments: one entry per distinct problem in the logs.
    # Optional/defaulted for backward compatibility with older payloads.
    issue_categories: list[CategoryAssessment] = Field(
        default_factory=list,
        description="SOURCE OF TRUTH: one full assessment per distinct problem "
        "(evidence + tool_run_examples + fix_suggestions). Do not put problems "
        "only in secondary_hypotheses.",
    )
    # Defaulted so weak Bedrock models (Nova Micro) may omit the field; repair
    # validator promotes categories/secondaries when present.
    primary_hypothesis: Hypothesis = Field(
        default_factory=lambda: Hypothesis(
            cause="incomplete model output",
            confidence=0,
            evidence="Structured response omitted primary_hypothesis",
        )
    )
    secondary_hypotheses: list[Hypothesis] = Field(
        default_factory=list,
        description="Short mirror of non-primary category causes only — not a "
        "substitute for full issue_categories entries",
    )
    blast_radius_assessment: str = Field(
        default="none identified",
        description="Which services/users are affected",
    )
    suggested_next_steps: list[str] = Field(default_factory=list)
    tool_run_examples: list[str] = Field(
        default_factory=list,
        description="Top-level copy-pasteable verification commands for the incident",
    )
    fix_suggestions: list[str] = Field(
        default_factory=list,
        description="Top-level human remediation steps (not auto-executed)",
    )
    confidence_note: Literal["low", "medium", "high"] = "low"

    @model_validator(mode="before")
    @classmethod
    def _repair_partial_payload(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return repair_diagnosis_payload(data)
        return data

    @field_validator("confidence_note", mode="before")
    @classmethod
    def _normalize_confidence_note(cls, value: Any) -> Any:
        if isinstance(value, str):
            normalized = value.strip().lower()
            return normalized if normalized in _CONFIDENCE_NOTES else "low"
        return value
