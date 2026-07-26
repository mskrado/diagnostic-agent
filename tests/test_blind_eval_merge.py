"""Tests for mixed-case (--merge) blind-eval helpers."""
from __future__ import annotations

import pytest

from app.tools.blind_eval import merge_cases, score_merged_case


def _case(cid: str, system: str, logs: list[str], **kwargs) -> dict:
    expected = kwargs.pop(
        "expected",
        {
            "cause_keywords": [system.split("-")[0]],
            "must_reference": [],
            "root_cause": f"{system} is broken",
        },
    )
    return {
        "id": cid,
        "system": system,
        "alert": {
            "alertname": f"{system.title()}Alert",
            "service": "platform-service",
            "severity": kwargs.pop("severity", "warning"),
        },
        "metrics": kwargs.pop("metrics", {}),
        "logs": logs,
        "expected": expected,
    }


def test_merge_cases_requires_two():
    with pytest.raises(ValueError, match="at least 2"):
        merge_cases([_case("a", "postgres", ["l1"])])


def test_merge_cases_interleaves_and_merges_metrics():
    a = _case(
        "postgres-connectivity",
        "postgresql",
        ["pg-a", "pg-b"],
        severity="critical",
        expected={
            "cause_keywords": ["postgres"],
            "root_cause": "pg down",
        },
    )
    b = _case(
        "redis-connection",
        "redis",
        ["rd-a"],
        metrics={"platform-service": {"heap_used_ratio": 0.9}},
        expected={
            "cause_keywords": ["redis"],
            "root_cause": "redis down",
        },
    )
    c = _case(
        "jvm-heap-oom",
        "jvm",
        ["jvm-a", "jvm-b", "jvm-c"],
        metrics={"platform-service": {"db_pool_pending": 3}},
        expected={
            "cause_keywords": ["heap", "oom"],
            "root_cause": "heap OOM",
        },
    )
    merged = merge_cases([a, b, c], seed=1)
    assert merged["system"] == "mixed"
    assert merged["merged_from"] == [
        "postgres-connectivity",
        "redis-connection",
        "jvm-heap-oom",
    ]
    assert merged["alert"]["alertname"] == "HighErrorRate"
    assert merged["alert"]["severity"] == "critical"
    assert set(merged["logs"]) == {"pg-a", "pg-b", "rd-a", "jvm-a", "jvm-b", "jvm-c"}
    assert len(merged["logs"]) == 6
    # Same seed → same shuffle
    assert merge_cases([a, b, c], seed=1)["logs"] == merged["logs"]
    assert merge_cases([a, b, c], seed=2)["logs"] != merged["logs"]
    assert merged["metrics"]["platform-service"]["heap_used_ratio"] == 0.9
    assert merged["metrics"]["platform-service"]["db_pool_pending"] == 3
    assert "postgresql" in merged["expected"]["root_cause"]
    assert "redis" in merged["expected"]["root_cause"]


def test_score_merged_hits_primary_and_secondary():
    sources = [
        _case(
            "postgres-connectivity",
            "postgresql",
            ["x"],
            expected={"cause_keywords": ["postgres", "database"], "root_cause": "pg"},
        ),
        _case(
            "redis-connection",
            "redis",
            ["y"],
            expected={"cause_keywords": ["redis", "lettuce"], "root_cause": "rd"},
        ),
    ]
    merged = merge_cases(sources, seed=0)
    diag = {
        "primary_hypothesis": {
            "cause": "PostgreSQL connection refused",
            "confidence": 80,
            "evidence": "refused on 5432",
        },
        "secondary_hypotheses": [
            {"cause": "Redis / Lettuce timeouts", "confidence": 60},
        ],
        "blast_radius_assessment": "wide",
        "suggested_next_steps": ["check postgres", "check redis"],
        "confidence_note": "medium",
    }
    score = score_merged_case(merged, diag)
    assert score["systems_total"] == 2
    assert score["systems_hit"] == 2
    assert score["systems_hit_rate"] == 1.0
    assert score["identified"] is True
    assert score["per_system"][0]["in_primary"] is True
    assert score["per_system"][1]["hit"] is True
    assert score["per_system"][1]["in_primary"] is False


def test_score_merged_partial_miss():
    sources = [
        _case(
            "postgres-connectivity",
            "postgresql",
            ["x"],
            expected={"cause_keywords": ["postgres"], "root_cause": "pg"},
        ),
        _case(
            "redis-connection",
            "redis",
            ["y"],
            expected={"cause_keywords": ["redis"], "root_cause": "rd"},
        ),
    ]
    merged = merge_cases(sources, seed=0)
    diag = {
        "primary_hypothesis": {
            "cause": "OpenAI health check failing",
            "confidence": 70,
            "evidence": "timeout",
        },
        "secondary_hypotheses": [{"cause": "Postgres is down", "confidence": 50}],
        "confidence_note": "medium",
    }
    score = score_merged_case(merged, diag)
    assert score["systems_hit"] == 1
    assert score["identified"] is False
    assert score["systems_hit_rate"] == 0.5


def test_build_judge_prompt_includes_full_diagnosis_json():
    from app.tools.blind_eval import build_judge_prompt

    case = _case(
        "postgres-connectivity",
        "postgresql",
        ["x"],
        expected={"cause_keywords": ["postgres"], "root_cause": "DB down"},
    )
    diag = {
        "_rag_used": True,
        "primary_hypothesis": {"cause": "primary only", "evidence": "e1"},
        "issue_categories": [
            {
                "category": "database",
                "cause": "Postgres connection refused",
                "confidence": 90,
                "evidence": "Connection refused",
            }
        ],
        "confidence_note": "high",
    }
    prompt = build_judge_prompt(case, diag)
    assert "ENTIRE MODEL diagnosis JSON" in prompt or "full JSON" in prompt
    assert "Postgres connection refused" in prompt
    assert "issue_categories" in prompt
    assert "_rag_used" not in prompt
    assert "KNOWN root cause" in prompt
    assert "DB down" in prompt


def test_build_judge_prompt_merged_credits_categories_and_skips_controls():
    from app.tools.blind_eval import build_judge_prompt, merge_cases

    sources = [
        _case(
            "postgres-connectivity",
            "postgresql",
            ["pg"],
            expected={"cause_keywords": ["postgres"], "root_cause": "Postgres unreachable"},
        ),
        _case(
            "redis-connection",
            "redis",
            ["rd"],
            expected={"cause_keywords": ["redis"], "root_cause": "Redis auth failed"},
        ),
        _case(
            "no-signal-control",
            "insufficient-data",
            ["ok"],
            expected={
                "cause_keywords": ["insufficient"],
                "root_cause": "Not enough signal",
            },
        ),
    ]
    merged = merge_cases(sources, seed=1)
    diag = {
        "primary_hypothesis": {"cause": "Redis auth", "evidence": "NOAUTH"},
        "issue_categories": [
            {
                "category": "database",
                "cause": "Postgres unreachable",
                "evidence": "Connection refused",
            },
            {
                "category": "cache",
                "cause": "Redis auth failed",
                "evidence": "NOAUTH",
            },
        ],
        "suggested_next_steps": ["Check SG", "Rotate redis password"],
    }
    prompt = build_judge_prompt(merged, diag)
    assert "MIXED multi-failure" in prompt
    assert "issue_categories" in prompt
    assert "Postgres unreachable" in prompt
    assert "Redis auth failed" in prompt
    assert "CONTROL" in prompt
    assert "no-signal-control" in prompt
    assert "do NOT require a matching hypothesis" in prompt
    assert "Postgres unreachable" in prompt  # in known checklist
    assert '"category": "database"' in prompt or "Postgres unreachable" in prompt
    assert "suggested_next_steps" in prompt
