"""Runbook scenario catalog, exercised through the host-facing tools.

Live end-to-end coverage moved to ``diag e2e --url ...``, which asserts the
same report shape and redaction probes against a running agent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.tools.scenarios import (
    build_alertmanager_payload,
    coverage_gaps,
    load_scenarios,
    read_runbook_text,
    runbook_files_on_disk,
)
from app.workspace import load as load_workspace

# This repository is itself a workspace: runbooks/ and runbook_scenarios.yaml
# resolve through the same conventions a host project uses.
_WS = load_workspace(Path(__file__).resolve().parent.parent)


@pytest.fixture(scope="module")
def scenarios():
    return load_scenarios(_WS)


def test_repo_resolves_as_a_workspace():
    assert _WS.runbooks_dir is not None
    assert _WS.scenarios_path is not None
    assert _WS.blind_eval_path is not None


def test_every_runbook_has_a_scenario():
    missing, extra = coverage_gaps(_WS)
    assert not missing, f"runbooks without scenarios: {sorted(missing)}"
    assert not extra, f"scenarios reference missing runbooks: {sorted(extra)}"


def test_scenario_ids_unique(scenarios):
    ids = [s["id"] for s in scenarios]
    assert len(ids) == len(set(ids)), "duplicate scenario id"


@pytest.mark.parametrize("scenario", load_scenarios(_WS), ids=lambda s: s["id"])
def test_rag_corpus_covers_scenario(scenario):
    """Offline: runbook file contains tokens the RAG query should ground on."""
    text = read_runbook_text(_WS, scenario["runbook"]).lower()
    for token in scenario.get("rag_must_contain") or []:
        assert token.lower() in text, (
            f"{scenario['id']}: token {token!r} not in {scenario['runbook']}"
        )


@pytest.mark.parametrize("scenario", load_scenarios(_WS), ids=lambda s: s["id"])
def test_synthetic_payload_shape(scenario):
    payload = build_alertmanager_payload(scenario)
    alert = payload["alerts"][0]
    labels = alert["labels"]
    assert alert["status"] == "firing"
    assert labels.get("alertname")
    assert labels.get("service")
    assert labels.get("severity") in ("warning", "critical")


def test_runbook_count_matches_scenarios(scenarios):
    assert len(scenarios) == len(runbook_files_on_disk(_WS))
