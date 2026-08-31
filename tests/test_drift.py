"""Workspace drift detection: each drift class and the exit-code contract."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from app.draft.verify import StubOracle
from app.drift.detect import detect
from app.drift.models import ERROR, NOTE
from app.drift.report import render
from app.install.client_scaffold import CLIENT_CI
from app.scan.models import (
    AlertRule,
    Findings,
    LokiEvidence,
    PrometheusEvidence,
    ScanEvidence,
    ServiceCandidate,
)
from app.workspace import load as load_workspace


def _write_workspace(
    root: Path,
    *,
    services: dict[str, dict] | None = None,
    scenarios: list[dict] | None = None,
    preset: str = "generic-prometheus",
    service_label: str = "service",
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    profile = root / "profile"
    profile.mkdir(exist_ok=True)
    (root / "agent.yaml").write_text(
        f"schema: 1\nextends: {preset}\nprofile: profile\n"
        "scenarios: scenarios.yaml\n",
        encoding="utf-8",
        newline="\n",
    )
    if services is None:
        services = {
            "api-gateway": {"kind": "gateway", "upstream": [], "downstream": ["app"]},
            "app": {"kind": "http", "upstream": ["api-gateway"], "downstream": []},
        }
    (profile / "service_map.yaml").write_text(
        yaml.safe_dump({"services": services}, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    (profile / "metrics_profile.yaml").write_text(
        f"extends: {preset}\n",
        encoding="utf-8",
        newline="\n",
    )
    (profile / "logs_profile.yaml").write_text(
        f"service_label: {service_label}\nuse_json_parser: true\n",
        encoding="utf-8",
        newline="\n",
    )
    if scenarios is None:
        scenarios = [
            {
                "id": "high-error-rate",
                "runbook": "runbook-high-error-rate.md",
                "labels": {
                    "alertname": "HighErrorRate",
                    "service": "app",
                    "severity": "critical",
                },
            }
        ]
    (root / "scenarios.yaml").write_text(
        yaml.safe_dump({"scenarios": scenarios}, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    return root


def _evidence(
    *,
    services: tuple[ServiceCandidate, ...] | None = None,
    rules: tuple[AlertRule, ...] | None = None,
    loki_reachable: bool = True,
) -> ScanEvidence:
    if services is None:
        services = (
            ServiceCandidate("api-gateway", has_metrics=True, has_logs=True),
            ServiceCandidate("app", has_metrics=True, has_logs=True),
        )
    if rules is None:
        rules = (
            AlertRule(name="HighErrorRate", source="prometheus", severity="critical"),
        )
    return ScanEvidence(
        generated_at="now",
        agent_version="test",
        prometheus=PrometheusEvidence(
            reachable=True,
            url="http://prometheus:9090",
            label_values={"service": tuple(s.name for s in services if s.has_metrics)},
            metric_names=("http_requests_total", "up"),
            rules=tuple(r for r in rules if r.source == "prometheus"),
        ),
        loki=LokiEvidence(
            reachable=loki_reachable,
            url="http://loki:3100",
            service_label="service",
            label_values={
                "service": tuple(s.name for s in services if s.has_logs),
            },
            rules=tuple(r for r in rules if r.source == "loki"),
        ),
        findings=Findings(services=services),
    )


def test_new_service_is_error(tmp_path):
    ws = load_workspace(_write_workspace(tmp_path / "ws"))
    evidence = _evidence(
        services=(
            ServiceCandidate("api-gateway", has_metrics=True, has_logs=True),
            ServiceCandidate("app", has_metrics=True, has_logs=True),
            ServiceCandidate("billing", has_metrics=True, has_logs=False),
        )
    )
    report = detect(evidence, ws)
    kinds = {(i.kind, i.name, i.severity) for i in report.items}
    assert ("new_service", "billing", ERROR) in kinds
    assert not report.ok


def test_gone_service_is_error(tmp_path):
    ws = load_workspace(
        _write_workspace(
            tmp_path / "ws",
            services={
                "app": {"kind": "http", "upstream": [], "downstream": []},
                "legacy": {"kind": "http", "upstream": [], "downstream": []},
            },
        )
    )
    evidence = _evidence(
        services=(ServiceCandidate("app", has_metrics=True, has_logs=True),)
    )
    report = detect(evidence, ws)
    assert any(i.kind == "gone_service" and i.name == "legacy" for i in report.errors)
    assert not report.ok


def test_uncovered_alert_is_error(tmp_path):
    ws = load_workspace(_write_workspace(tmp_path / "ws"))
    evidence = _evidence(
        rules=(
            AlertRule(name="HighErrorRate", source="prometheus"),
            AlertRule(name="DiskFull", source="prometheus"),
        )
    )
    report = detect(evidence, ws)
    assert any(i.kind == "uncovered_alert" and i.name == "DiskFull" for i in report.errors)


def test_unused_scenario_is_note_only(tmp_path):
    ws = load_workspace(
        _write_workspace(
            tmp_path / "ws",
            scenarios=[
                {
                    "id": "high-error-rate",
                    "runbook": "runbook-high-error-rate.md",
                    "labels": {"alertname": "HighErrorRate"},
                },
                {
                    "id": "old-alert",
                    "runbook": "runbook-old.md",
                    "labels": {"alertname": "RetiredAlert"},
                },
            ],
        )
    )
    evidence = _evidence(
        rules=(AlertRule(name="HighErrorRate", source="prometheus"),)
    )
    report = detect(evidence, ws, oracle=None)
    assert report.ok
    assert any(
        i.kind == "unused_scenario" and i.name == "RetiredAlert" and i.severity == NOTE
        for i in report.notes
    )


def test_dead_template_is_error(tmp_path):
    ws = load_workspace(_write_workspace(tmp_path / "ws"))
    evidence = _evidence()
    oracle = StubOracle(promql_ok=lambda q: False, logql_ok=lambda q: True)
    report = detect(evidence, ws, oracle=oracle)
    assert any(i.kind == "dead_template" for i in report.errors)
    assert not report.ok


def test_dead_log_selector_is_error(tmp_path):
    ws = load_workspace(_write_workspace(tmp_path / "ws", service_label="app"))
    evidence = _evidence()
    oracle = StubOracle(promql_ok=lambda q: True, logql_ok=lambda q: False)
    report = detect(evidence, ws, oracle=oracle)
    assert any(
        i.kind == "dead_log_selector" and i.name == "app" for i in report.errors
    )


def test_no_oracle_skips_template_checks_with_warning(tmp_path):
    ws = load_workspace(_write_workspace(tmp_path / "ws"))
    evidence = _evidence()
    report = detect(evidence, ws, oracle=None)
    assert report.ok
    assert any("no live oracle" in w for w in report.warnings)
    assert not any(i.kind in ("dead_template", "dead_log_selector") for i in report.items)


def test_clean_workspace_is_ok(tmp_path):
    ws = load_workspace(_write_workspace(tmp_path / "ws"))
    evidence = _evidence()
    oracle = StubOracle(promql_ok=lambda q: True, logql_ok=lambda q: True)
    report = detect(evidence, ws, oracle=oracle)
    assert report.ok
    assert not report.errors
    text = render(report)
    assert "OK" in text
    assert "no drift detected" in text


def test_bundle_cli_exit_codes(tmp_path, monkeypatch, capsys):
    from app.drift.cli import run_drift

    root = _write_workspace(tmp_path / "ws")
    clean = _evidence()
    dirty = _evidence(
        services=(
            ServiceCandidate("api-gateway", has_metrics=True, has_logs=True),
            ServiceCandidate("app", has_metrics=True, has_logs=True),
            ServiceCandidate("orphan", has_metrics=True),
        )
    )
    clean_path = tmp_path / "clean.json"
    dirty_path = tmp_path / "dirty.json"
    clean_path.write_text(json.dumps(clean.to_dict()), encoding="utf-8")
    dirty_path.write_text(json.dumps(dirty.to_dict()), encoding="utf-8")

    class _EmptySettings:
        prometheus_url = ""
        loki_url = ""

    monkeypatch.setattr("app.config.Settings", lambda: _EmptySettings())

    def _ns(**kwargs):
        defaults = dict(
            workspace=str(root),
            bundle="",
            prometheus_url="",
            loki_url="",
            alertmanager_url="",
            timeout=10.0,
            lookback_minutes=60,
            window="5m",
            out="",
            as_json=False,
            no_oracle=True,
        )
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    assert run_drift(_ns(bundle=str(clean_path))) == 0
    assert run_drift(_ns(bundle=str(dirty_path))) == 1
    out = capsys.readouterr().out
    assert "orphan" in out


def test_client_ci_scaffold_includes_drift_step():
    assert "Drift check (optional)" in CLIENT_CI
    assert "diag drift" in CLIENT_CI
    assert "DRIFT_BUNDLE" in CLIENT_CI
