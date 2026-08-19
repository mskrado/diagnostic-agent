from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from app import config as config_mod
from app.delivery.audit import write_audit_record
from app.dependency_map import DependencyMap
from app.graph.nodes import DiagnosticNodes
from app.graph.routing import normalize_severity, should_route
from app.graph.schema import Diagnosis, Hypothesis


def test_normalize_severity_maps_common_labels():
    assert normalize_severity("critical") == "SEV1"
    assert normalize_severity("warning") == "SEV3"
    assert normalize_severity("info") == "SEV4"
    assert normalize_severity("unknown-value") == "UNKNOWN"


def test_should_route_default_safe_when_disabled(monkeypatch):
    monkeypatch.setenv("AGENT_ROUTING_ENABLED", "false")
    config_mod.settings = config_mod.Settings()
    state = {
        "severity": "critical",
        "severity_normalized": "SEV1",
        "hypotheses": {"confidence_note": "low"},
        "rag_context": "matched runbook",
    }
    assert should_route(state) == "report"


def test_should_route_escalates_for_sev1_when_enabled(monkeypatch):
    monkeypatch.setenv("AGENT_ROUTING_ENABLED", "true")
    config_mod.settings = config_mod.Settings()
    state = {
        "severity": "critical",
        "severity_normalized": "SEV1",
        "hypotheses": {"confidence_note": "high"},
        "rag_context": "matched runbook",
    }
    assert should_route(state) == "escalate"


def test_should_route_escalates_for_low_confidence(monkeypatch):
    monkeypatch.setenv("AGENT_ROUTING_ENABLED", "true")
    config_mod.settings = config_mod.Settings()
    state = {
        "severity": "warning",
        "severity_normalized": "SEV3",
        "hypotheses": {"confidence_note": "low"},
        "rag_context": "matched runbook",
    }
    assert should_route(state) == "escalate"


def test_should_route_marks_execute_candidate(monkeypatch):
    monkeypatch.setenv("AGENT_ROUTING_ENABLED", "true")
    config_mod.settings = config_mod.Settings()
    state = {
        "severity": "warning",
        "severity_normalized": "SEV3",
        "hypotheses": {"confidence_note": "high"},
        "rag_context": "matched runbook",
    }
    assert should_route(state) == "execute"


def test_detect_adds_normalized_severity():
    dep_map = DependencyMap({"services": {"platform-service": {"kind": "monolith"}}})
    nodes = DiagnosticNodes(None, None, None, dep_map, None, None)
    out = nodes.detect(
        {
            "raw_labels": {"service": "platform-service", "severity": "critical"},
        }
    )
    assert out["severity"] == "critical"
    assert out["severity_normalized"] == "SEV1"


def test_report_records_route_decision(monkeypatch):
    monkeypatch.setenv("AGENT_ROUTING_ENABLED", "true")
    config_mod.settings = config_mod.Settings()
    diagnosis = Diagnosis(
        primary_hypothesis=Hypothesis(
            cause="DB pool exhausted",
            confidence=85,
            evidence="db_pool_pending=12",
        ),
        secondary_hypotheses=[],
        blast_radius_assessment="platform-service only",
        suggested_next_steps=["Check HikariCP metrics"],
        confidence_note="high",
    )
    nodes = DiagnosticNodes(
        prom=MagicMock(),
        loki=MagicMock(),
        grafana=MagicMock(),
        dep_map=MagicMock(),
        rag=MagicMock(),
        llm=MagicMock(),
    )
    state = {
        "service": "platform-service",
        "alert_type": "HighErrorRate",
        "severity": "warning",
        "severity_normalized": "SEV3",
        "module_hint": "",
        "dependencies": [],
        "blast_radius": [],
        "prom_data": {},
        "loki_logs": [],
        "log_source": {},
        "rag_context": "runbook chunk",
        "hypotheses": diagnosis.model_dump(),
        "llm_system_prompt": "sys",
        "llm_user_prompt": "user",
        "llm_token_usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }
    reported = nodes.report(state)
    assert reported["route"] == "execute"
    assert reported["report"]["route_decision"] == "execute"
    assert reported["report"]["severity_normalized"] == "SEV3"


def test_audit_record_includes_route_decision(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_ROUTING_ENABLED", "true")
    config_mod.settings = config_mod.Settings()
    monkeypatch.setattr("app.delivery.audit.settings.audit_log_dir", str(tmp_path))
    report = {
        "service": "platform-service",
        "alert_type": "HighErrorRate",
        "severity": "warning",
        "severity_normalized": "SEV3",
        "route_decision": "execute",
        "llm_exchange": {
            "system_prompt": "sys",
            "user_prompt": "user",
            "rag_context": "ctx",
            "rag_used": True,
            "token_usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        },
    }
    path = write_audit_record(report, llm_raw="{}")
    line = Path(path).read_text(encoding="utf-8").strip()
    assert '"route_decision": "execute"' in line
