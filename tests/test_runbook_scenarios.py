"""Runbook scenario catalog + optional live E2E against diagnostic-agent /alert."""
from __future__ import annotations

import os

import httpx
import pytest

from tests.runbook_scenarios_loader import (
    build_alertmanager_payload,
    coverage_gaps,
    load_scenarios,
    read_runbook_text,
    runbook_files_on_disk,
)


@pytest.fixture(scope="module")
def scenarios():
    return load_scenarios()


def test_every_runbook_has_a_scenario():
    missing, extra = coverage_gaps()
    assert not missing, f"runbooks without scenarios: {sorted(missing)}"
    assert not extra, f"scenarios reference missing runbooks: {sorted(extra)}"


def test_scenario_ids_unique(scenarios):
    ids = [s["id"] for s in scenarios]
    assert len(ids) == len(set(ids)), "duplicate scenario id"


@pytest.mark.parametrize("scenario", load_scenarios(), ids=lambda s: s["id"])
def test_rag_corpus_covers_scenario(scenario):
    """Offline: runbook file contains tokens the RAG query should ground on."""
    text = read_runbook_text(scenario["runbook"]).lower()
    for token in scenario.get("rag_must_contain") or []:
        assert token.lower() in text, (
            f"{scenario['id']}: token {token!r} not in {scenario['runbook']}"
        )


@pytest.mark.parametrize("scenario", load_scenarios(), ids=lambda s: s["id"])
def test_synthetic_payload_shape(scenario):
    payload = build_alertmanager_payload(scenario)
    alert = payload["alerts"][0]
    labels = alert["labels"]
    assert alert["status"] == "firing"
    assert labels.get("alertname")
    assert labels.get("service")
    assert labels.get("severity") in ("warning", "critical")


@pytest.mark.e2e
@pytest.mark.parametrize("scenario", load_scenarios(), ids=lambda s: s["id"])
def test_live_alert_scenario(scenario):
    """Live E2E: POST synthetic alert; assert structured report + no tenant leak.

    Run against a running stack:
      set AGENT_E2E_URL=http://localhost:8001
      pytest -m e2e tests/test_runbook_scenarios.py
    """
    base = os.environ.get("AGENT_E2E_URL", "").rstrip("/")
    if not base:
        pytest.skip("set AGENT_E2E_URL (e.g. http://localhost:8001) for live E2E")

    payload = build_alertmanager_payload(scenario)
    labels = scenario["labels"]
    timeout = float(os.environ.get("AGENT_E2E_TIMEOUT_SEC", "300"))

    with httpx.Client(timeout=timeout) as client:
        health = client.get(f"{base}/health")
        health.raise_for_status()
        assert health.json().get("agent_initialized") is True

        resp = client.post(f"{base}/alert", json=payload)
        resp.raise_for_status()
        body = resp.json()

    assert body.get("count", 0) >= 1, body
    report = body["reports"][0]
    assert report.get("service") == labels["service"]
    assert report.get("alert_type") == labels["alertname"]
    assert report.get("severity") == labels["severity"]
    assert "diagnosis" in report
    assert "evidence" in report

    raw = resp.text.lower()
    assert "tenant-smoke-test" not in raw
    assert "550e8400-e29b-41d4-a716-446655440000" not in raw


def test_runbook_count_matches_scenarios(scenarios):
    assert len(scenarios) == len(runbook_files_on_disk())
