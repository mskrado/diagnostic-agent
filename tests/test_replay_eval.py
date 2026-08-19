from __future__ import annotations

import json
from pathlib import Path

import yaml

from app import config as config_mod
from app.tools import replay_eval
from app.workspace import load as load_workspace


def test_replay_case_defaults_to_escalate_for_critical(monkeypatch):
    monkeypatch.setenv("AGENT_ROUTING_ENABLED", "true")
    config_mod.settings = config_mod.Settings()
    result = replay_eval.replay_case(
        {
            "id": "db-pool",
            "labels": {
                "alertname": "HikariPoolExhaustion",
                "service": "platform-service",
                "severity": "critical",
            },
            "runbook": "runbook-db-pool-exhaustion.md",
        }
    )
    assert result["route_actual"] == "escalate"
    assert result["passed"] is True


def test_replay_case_execute_uses_expected_runbook(monkeypatch):
    monkeypatch.setenv("AGENT_ROUTING_ENABLED", "true")
    config_mod.settings = config_mod.Settings()
    result = replay_eval.replay_case(
        {
            "id": "redis",
            "labels": {
                "alertname": "RedisErrorsInLogs",
                "service": "platform-service",
                "severity": "warning",
            },
            "runbook": "runbook-redis-connection-errors.md",
            "replay": {
                "expected_route": "execute",
                "expected_runbook": "runbook-redis-connection-errors.md",
                "confidence_note": "high",
            },
        }
    )
    assert result["route_actual"] == "execute"
    assert result["runbook_actual"] == "runbook-redis-connection-errors.md"
    assert result["passed"] is True


def test_replay_main_writes_results_file(tmp_path, monkeypatch):
    dataset = tmp_path / "scenarios.yaml"
    dataset.write_text(
        yaml.safe_dump(
            {
                "scenarios": [
                    {
                        "id": "critical-escalate",
                        "labels": {
                            "alertname": "PostgresErrorsInLogs",
                            "service": "platform-service",
                            "severity": "critical",
                        },
                        "runbook": "runbook-postgres-connectivity.md",
                    },
                    {
                        "id": "warning-execute",
                        "labels": {
                            "alertname": "RedisErrorsInLogs",
                            "service": "platform-service",
                            "severity": "warning",
                        },
                        "runbook": "runbook-redis-connection-errors.md",
                        "replay": {
                            "expected_route": "execute",
                            "expected_runbook": "runbook-redis-connection-errors.md",
                            "confidence_note": "high",
                        },
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    ws = load_workspace(Path(__file__).resolve().parent.parent)
    rc = replay_eval.main(
        ["--dataset", str(dataset), "--out", str(tmp_path / "out")], workspace=ws
    )
    assert rc == 0
    files = sorted((tmp_path / "out").glob("replay-eval-*.json"))
    assert files, "expected replay result JSON"
    doc = json.loads(files[0].read_text(encoding="utf-8"))
    assert doc["summary"]["failed"] == 0
    assert {r["id"] for r in doc["results"]} == {"critical-escalate", "warning-execute"}
