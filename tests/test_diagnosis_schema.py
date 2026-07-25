"""Tests for diagnosis schema fields used by structured LLM output."""
from __future__ import annotations

from app.graph.prompts import build_system_prompt
from app.graph.schema import CategoryAssessment, Diagnosis, Hypothesis


def test_system_prompt_requires_tool_runs_and_fix_suggestions():
    # Built per call so the active integration profile is applied (the module
    # attribute would otherwise be snapshotted at import time).
    prompt = build_system_prompt()
    assert "tool_run_examples" in prompt
    assert "fix_suggestions" in prompt
    assert "copy-pasteable" in prompt.lower() or "copy-paste" in prompt.lower()
    assert "Do NOT auto-remediate" in prompt
    assert "docker compose ps" in prompt
    assert "LogQL" in prompt or "loki" in prompt.lower()
    assert "NEVER put an issue only under" in prompt
    assert "SOURCE OF TRUTH" in prompt
    assert "EVERY category" in prompt or "every category" in prompt.lower()


def test_core_prompt_invariants_hold_for_any_profile():
    """Safety rules live in agent code and cannot be dropped by a profile."""
    prompt = build_system_prompt(
        platform_description="anything", tool_run_hints="anything"
    )
    assert "Do NOT auto-remediate" in prompt
    assert "SOURCE OF TRUTH" in prompt
    assert "NEVER put an issue only under" in prompt


def test_diagnosis_schema_accepts_tool_and_fix_fields():
    diag = Diagnosis(
        issue_categories=[
            CategoryAssessment(
                category="database",
                cause="Postgres connection refused",
                confidence=90,
                evidence="Connection to postgres:5432 refused",
                suggested_next_step="Check postgres container",
                tool_run_examples=[
                    "docker compose ps postgres",
                    'curl -sG http://localhost:3100/loki/api/v1/query_range '
                    '--data-urlencode \'query={service="platform-service"} |~ "(?i)postgres"\'',
                ],
                fix_suggestions=[
                    "Confirm postgres container is healthy; restart only if down "
                    "(brief downtime).",
                    "Verify SPRING_DATASOURCE_PASSWORD matches POSTGRES_PASSWORD.",
                ],
            )
        ],
        primary_hypothesis=Hypothesis(
            cause="Postgres connection refused",
            confidence=90,
            evidence="Connection to postgres:5432 refused",
        ),
        secondary_hypotheses=[],
        blast_radius_assessment="All DB-backed modules",
        suggested_next_steps=["Check postgres container health"],
        tool_run_examples=["docker logs publishi-postgres --tail 100"],
        fix_suggestions=["Restart postgres if container is exited."],
        confidence_note="high",
    )
    payload = diag.model_dump()
    assert payload["tool_run_examples"][0].startswith("docker logs")
    assert payload["fix_suggestions"]
    assert payload["issue_categories"][0]["tool_run_examples"]
    assert payload["issue_categories"][0]["fix_suggestions"]


def test_diagnosis_schema_defaults_new_lists_empty():
    diag = Diagnosis(
        primary_hypothesis=Hypothesis(cause="x", confidence=50),
        blast_radius_assessment="none",
        suggested_next_steps=[],
        confidence_note="low",
    )
    assert diag.tool_run_examples == []
    assert diag.fix_suggestions == []
    assert diag.issue_categories == []
