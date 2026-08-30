"""Evidence collection, scrubbing, analysis, and reporting for ``diag scan``."""
from __future__ import annotations

import json

import httpx
import pytest

from app.scan import collect as collect_mod
from app.scan import scrub
from app.scan.analyze import analyze
from app.scan.collect import ScanOptions
from app.scan.models import (
    AlertRule,
    Findings,
    LogSample,
    LokiEvidence,
    PrometheusEvidence,
    ScanEvidence,
    ScrapeTarget,
)
from app.scan.report import render


# -- scrub -------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected_absent",
    [
        ("contact ops@example.com now", "ops@example.com"),
        ("Authorization: Bearer abc.def-123", "abc.def-123"),
        ("key AKIAIOSFODNN7EXAMPLE rotated", "AKIAIOSFODNN7EXAMPLE"),
        ('{"password": "hunter2"}', "hunter2"),
        ('{"tenant_id": "tenant-42"}', "tenant-42"),
        ("id 550e8400-e29b-41d4-a716-446655440000", "550e8400-e29b"),
        ("postgres://user:s3cret@db:5432/app", "s3cret"),
        ("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig", "eyJhbGciOiJIUzI1NiJ9"),
    ],
)
def test_scrub_removes_sensitive_values(raw, expected_absent):
    assert expected_absent not in scrub.scrub_text(raw)


def test_scrub_keeps_surrounding_text():
    out = scrub.scrub_text('{"tenant_id": "tenant-42", "service": "app"}')
    assert '"service": "app"' in out
    assert "tenant_id" in out


def test_census_counts_lines_and_matches():
    lines = [
        "a@b.com and c@d.com",
        "e@f.com",
        "no secrets here",
    ]
    hits = {hit.name: hit for hit in scrub.census(lines)}
    assert hits["email"].lines == 2
    assert hits["email"].matches == 3
    assert "jwt" not in hits


def test_scrub_lines_applies_workspace_rules_then_builtins():
    """The Spring example profile (pinned in conftest) redacts tenant tokens."""
    out = scrub.scrub_lines(["tenant-abc123 hit ops@example.com"])
    assert "tenant-abc123" not in out[0]
    assert "ops@example.com" not in out[0]


def test_workspace_scrubber_falls_back_when_profile_unusable(monkeypatch):
    import app.delivery.redact as redact_mod

    def boom(_text):
        raise RuntimeError("no profile")

    monkeypatch.setattr(redact_mod, "redact_text", boom)
    assert scrub.workspace_scrubber()("untouched") == "untouched"


# -- line filter / selector extraction ---------------------------------------
def test_extract_line_filters_from_logql():
    expr = (
        'sum(count_over_time({service="app"} '
        '|~ "(?i)(postgres|jdbc).*(refused|timeout)" [5m])) > 0'
    )
    assert collect_mod._extract_line_filters(expr) == (
        "(?i)(postgres|jdbc).*(refused|timeout)",
    )


def test_extract_line_filters_handles_multiple_and_dedupes():
    expr = '{a="b"} |= "boom" |~ "kaboom" |= "boom"'
    assert collect_mod._extract_line_filters(expr) == ("boom", "kaboom")


def test_extract_line_filters_ignores_promql():
    assert collect_mod._extract_line_filters("rate(http_requests_total[5m]) > 0.1") == ()


def test_extract_selector_services_prefers_labels_and_selector():
    expr = '{service=~"api-gateway|platform-service"} |~ "boom"'
    services = collect_mod._extract_selector_services(expr, {"service": "security"})
    assert services[0] == "security"
    assert "api-gateway" in services and "platform-service" in services


# -- collect -----------------------------------------------------------------
def _prometheus_payloads():
    return {
        "/api/v1/label/__name__/values": [
            "up",
            "http_server_requests_seconds_count",
            "hikaricp_connections_pending",
        ],
        "/api/v1/status/buildinfo": {"version": "2.51.0"},
        "/api/v1/labels": ["__name__", "job", "service"],
        "/api/v1/label/service/values": ["api-gateway", "platform-service", "postgres"],
        "/api/v1/label/job/values": ["platform-service", "prometheus"],
        "/api/v1/targets": {
            "activeTargets": [
                {
                    "health": "up",
                    "labels": {
                        "job": "platform-service",
                        "instance": "platform-service:8080",
                        "service": "platform-service",
                    },
                }
            ]
        },
        "/api/v1/rules": {
            "groups": [
                {
                    "name": "app",
                    "rules": [
                        {
                            "type": "alerting",
                            "name": "HighErrorRate",
                            "query": 'rate(http_server_requests_seconds_count{status=~"5.."}[5m]) > 1',
                            "duration": 300,
                            "labels": {"severity": "critical"},
                            "annotations": {"runbook": "runbook-high-error-rate.md"},
                        }
                    ],
                }
            ]
        },
    }


class _StubResp:
    def __init__(self, payload=None, text=""):
        self._payload = payload
        self.text = text
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _install_stack(monkeypatch, *, loki_lines=None, loki_labels=None):
    """Fake a full Prometheus + Loki + Alertmanager stack over httpx.

    Dispatch is prefix-first: Loki's ``/loki/api/v1/labels`` also ends with
    Prometheus's ``/api/v1/labels``, so suffix matching alone would answer Loki
    with Prometheus payloads.
    """
    prom = _prometheus_payloads()
    loki_labels = loki_labels if loki_labels is not None else ["service", "level"]
    loki_lines = loki_lines if loki_lines is not None else []

    def handler(url, params=None):
        if "/loki/api/v1/" not in url and "/api/v2/" not in url:
            for path, payload in prom.items():
                if url.endswith(path):
                    return _StubResp({"data": payload})
            raise AssertionError(f"unexpected prometheus url {url}")
        if url.endswith("/loki/api/v1/labels"):
            return _StubResp({"data": loki_labels})
        if url.endswith("/loki/api/v1/label/service/values"):
            return _StubResp({"data": ["api-gateway", "platform-service"]})
        if url.endswith("/loki/api/v1/rules"):
            return _StubResp(
                text=(
                    "ns:\n"
                    "  - name: log-alerts\n"
                    "    rules:\n"
                    "      - alert: PostgresErrorsInLogs\n"
                    '        expr: sum(count_over_time({service="platform-service"} '
                    '|~ "(?i)(postgres|jdbc)" [5m])) > 0\n'
                    "        labels:\n"
                    "          severity: warning\n"
                )
            )
        if url.endswith("/loki/api/v1/query_range"):
            query = (params or {}).get("query", "")
            if "|~" in query:
                # Dependency-name probe: postgres errors land in the app stream.
                return _StubResp(
                    {
                        "data": {
                            "result": [
                                {
                                    "stream": {"service": "platform-service"},
                                    "values": [["3", "jdbc connection refused"]],
                                }
                            ]
                        }
                    }
                )
            value = "platform-service" if "platform-service" in query else "api-gateway"
            return _StubResp(
                {
                    "data": {
                        "result": [
                            {
                                "stream": {"service": value},
                                "values": [
                                    [str(i + 1), line]
                                    for i, line in enumerate(loki_lines)
                                ],
                            }
                        ]
                    }
                }
            )
        if url.endswith("/api/v2/status"):
            return _StubResp(
                {
                    "versionInfo": {"version": "0.27.0"},
                    "config": {
                        "original": "receivers:\n- slack_configs:\n"
                        "  - api_url: https://hooks.slack.com/T00/B00/SUPERSECRET\n"
                    },
                }
            )
        if url.endswith("/api/v2/receivers"):
            return _StubResp([{"name": "diagnostic-agent"}])
        if url.endswith("/api/v2/alerts"):
            return _StubResp([{"labels": {"alertname": "HighErrorRate"}}])
        raise AssertionError(f"unexpected url {url} params={params}")

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, params=None):
            return handler(url, params)

    monkeypatch.setattr(httpx, "Client", _Client)


_JSON_LINE = json.dumps(
    {
        "@timestamp": "2026-08-30T10:00:00Z",
        "level": "ERROR",
        "logger_name": "com.example.platform.media.MediaService",
        "message": "upload failed for ops@example.com tenant_id=tenant-9",
    }
)


def test_collect_builds_evidence_from_live_stack(monkeypatch):
    _install_stack(monkeypatch, loki_lines=[_JSON_LINE, "plain text line"])
    options = ScanOptions(
        prometheus_url="http://prometheus:9090",
        loki_url="http://loki:3100",
        alertmanager_url="http://alertmanager:9093",
        keep_lines=True,
        workspace="",
    )
    evidence = collect_mod.collect_evidence(options)

    assert evidence.prometheus.reachable
    assert evidence.prometheus.version == "2.51.0"
    assert evidence.prometheus.metric_count == 3
    assert evidence.prometheus.label_values["service"] == (
        "api-gateway",
        "platform-service",
        "postgres",
    )
    assert [t.job for t in evidence.prometheus.targets] == ["platform-service"]

    rule = evidence.prometheus.rules[0]
    assert rule.name == "HighErrorRate"
    assert rule.severity == "critical"
    assert rule.runbook == "runbook-high-error-rate.md"
    assert rule.duration == "300"

    assert evidence.loki.reachable
    assert evidence.loki.service_label == "service"
    assert evidence.loki.level_field == "level"

    log_rule = evidence.loki.rules[0]
    assert log_rule.name == "PostgresErrorsInLogs"
    assert log_rule.source == "loki"
    assert log_rule.line_filters == ("(?i)(postgres|jdbc)",)

    assert evidence.alertmanager.reachable
    assert evidence.alertmanager.firing == {"HighErrorRate": 1}
    assert evidence.alertmanager.receivers == ("diagnostic-agent",)


def test_collect_scrubs_sampled_lines(monkeypatch):
    _install_stack(monkeypatch, loki_lines=[_JSON_LINE])
    evidence = collect_mod.collect_evidence(
        ScanOptions(
            prometheus_url="http://prometheus:9090",
            loki_url="http://loki:3100",
            keep_lines=True,
        )
    )
    held = "\n".join(line for s in evidence.loki.samples for line in s.lines)
    assert held, "expected sample lines to be kept"
    assert "ops@example.com" not in held
    assert "tenant-9" not in held
    # The census still reports what was there before scrubbing.
    assert {hit.name for hit in evidence.loki.secrets} >= {"email"}


def test_collect_omits_lines_unless_requested(monkeypatch):
    _install_stack(monkeypatch, loki_lines=[_JSON_LINE])
    evidence = collect_mod.collect_evidence(
        ScanOptions(prometheus_url="http://prometheus:9090", loki_url="http://loki:3100")
    )
    assert all(sample.lines == () for sample in evidence.loki.samples)
    assert evidence.loki.samples[0].line_count == 1


def test_collect_discovers_log_service_redirect(monkeypatch):
    """postgres has metrics but no stream; its errors are in the app stream."""
    _install_stack(monkeypatch, loki_lines=[_JSON_LINE])
    evidence = collect_mod.collect_evidence(
        ScanOptions(prometheus_url="http://prometheus:9090", loki_url="http://loki:3100")
    )
    assert evidence.loki.log_service_hints["postgres"] == ("platform-service",)


def test_collect_skips_samples_when_disabled(monkeypatch):
    _install_stack(monkeypatch, loki_lines=[_JSON_LINE])
    evidence = collect_mod.collect_evidence(
        ScanOptions(
            prometheus_url="http://prometheus:9090",
            loki_url="http://loki:3100",
            include_samples=False,
        )
    )
    assert evidence.loki.samples == ()
    assert evidence.loki.log_service_hints == {}
    assert any("skipped" in note for note in evidence.loki.notes)


def test_collect_marks_prometheus_unreachable(monkeypatch):
    def handler(*args, **kwargs):
        raise httpx.ConnectError("refused")

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        get = handler

    monkeypatch.setattr(httpx, "Client", _Client)
    evidence = collect_mod.collect_evidence(ScanOptions(prometheus_url="http://nope:9090"))
    assert evidence.prometheus.reachable is False
    assert evidence.loki.reachable is False


def test_collect_without_loki_url_notes_it(monkeypatch):
    _install_stack(monkeypatch)
    evidence = collect_mod.collect_evidence(ScanOptions(prometheus_url="http://prometheus:9090"))
    assert evidence.loki.notes == ("no Loki URL configured",)
    assert evidence.alertmanager.notes == ("no Alertmanager URL configured",)


def test_evidence_serialises_to_json(monkeypatch):
    _install_stack(monkeypatch, loki_lines=[_JSON_LINE])
    evidence = collect_mod.collect_evidence(
        ScanOptions(
            prometheus_url="http://prometheus:9090",
            loki_url="http://loki:3100",
            alertmanager_url="http://alertmanager:9093",
            keep_lines=True,
        )
    )
    payload = json.loads(json.dumps(evidence.to_dict()))
    assert payload["schema"] == 1
    assert payload["prometheus"]["reachable"] is True
    assert set(payload) == {
        "schema",
        "generated_at",
        "agent_version",
        "workspace",
        "prometheus",
        "loki",
        "alertmanager",
        "findings",
    }
    # The Alertmanager config embeds webhook credentials; it must never be held.
    assert "config" not in payload["alertmanager"]
    assert "SUPERSECRET" not in json.dumps(payload)


# -- analyze -----------------------------------------------------------------
def _evidence_for_analysis(**overrides) -> ScanEvidence:
    prometheus = PrometheusEvidence(
        reachable=True,
        metric_count=3,
        metric_names=("up", "http_server_requests_seconds_count", "jvm_memory_used_bytes"),
        label_values={"service": ("api-gateway", "platform-service", "postgres", "loki")},
        targets=(ScrapeTarget(job="app", instance="app:8080", health="up"),),
        rules=(AlertRule(name="HighErrorRate", source="prometheus"),),
    )
    loki = LokiEvidence(
        reachable=True,
        service_label="service",
        label_values={"service": ("api-gateway", "platform-service")},
        samples=(LogSample(stream_value="platform-service", line_count=5, json_lines=5),),
        log_service_hints={"postgres": ("platform-service",)},
        rules=(
            AlertRule(
                name="PostgresErrorsInLogs",
                source="loki",
                line_filters=("(?i)postgres",),
            ),
        ),
    )
    base = {"prometheus": prometheus, "loki": loki}
    base.update(overrides)
    return ScanEvidence(generated_at="now", agent_version="test", **base)


def test_analyze_cross_references_services():
    findings = analyze(_evidence_for_analysis(), ScanOptions(prometheus_url="x"))
    by_name = {s.name: s for s in findings.services}

    assert "loki" not in by_name, "observability infrastructure is not a candidate"
    assert by_name["platform-service"].has_metrics
    assert by_name["platform-service"].has_logs
    assert by_name["postgres"].has_metrics
    assert by_name["postgres"].has_logs is False
    assert by_name["postgres"].kind_hints == ("database",)
    assert by_name["postgres"].log_services_hint == ("platform-service",)
    assert by_name["api-gateway"].kind_hints == ("gateway",)


def test_analyze_reports_naming_markers():
    findings = analyze(_evidence_for_analysis(), ScanOptions(prometheus_url="x"))
    markers = {m.metric: m.present for m in findings.naming_markers}
    assert markers["http_server_requests_seconds_count"] is True
    assert markers["http_requests_total"] is False


def test_analyze_notes_metrics_without_logs():
    findings = analyze(_evidence_for_analysis(), ScanOptions(prometheus_url="x"))
    joined = " ".join(findings.notes)
    assert "no log stream of their own" in joined
    assert "line filters" in joined


def test_analyze_alert_coverage_against_workspace(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "runbooks").mkdir(parents=True)
    (workspace / "agent.yaml").write_text("schema: 1\n", encoding="utf-8")
    (workspace / "scenarios.yaml").write_text(
        "scenarios:\n"
        "  - id: high-error-rate\n"
        "    runbook: runbook-high-error-rate.md\n"
        "    labels:\n"
        "      alertname: HighErrorRate\n"
        "      service: app\n"
        "      severity: critical\n",
        encoding="utf-8",
    )
    findings = analyze(
        _evidence_for_analysis(),
        ScanOptions(prometheus_url="x", workspace=str(workspace)),
    )
    assert findings.covered_alerts == ("HighErrorRate",)
    assert findings.uncovered_alerts == ("PostgresErrorsInLogs",)


def test_analyze_reports_no_coverage_without_workspace_scenarios(tmp_path):
    findings = analyze(
        _evidence_for_analysis(),
        ScanOptions(prometheus_url="x", workspace=str(tmp_path)),
    )
    assert findings.covered_alerts == ()
    assert findings.uncovered_alerts == ()


# -- report ------------------------------------------------------------------
def test_render_includes_every_section():
    evidence = _evidence_for_analysis()
    evidence = ScanEvidence(
        generated_at=evidence.generated_at,
        agent_version=evidence.agent_version,
        prometheus=evidence.prometheus,
        loki=evidence.loki,
        alertmanager=evidence.alertmanager,
        findings=analyze(evidence, ScanOptions(prometheus_url="x")),
    )
    out = render(evidence)

    for heading in (
        "sources",
        "service candidates",
        "metric naming markers",
        "alerts",
        "log shape",
        "observations",
    ):
        assert heading in out
    assert "ok   prometheus" in out
    assert "logs_under=platform-service" in out
    assert out.isascii(), "report must survive a Windows console"


def test_render_marks_unreachable_prometheus():
    evidence = ScanEvidence(
        generated_at="now",
        agent_version="test",
        prometheus=PrometheusEvidence(reachable=False, url="http://nope:9090"),
        findings=Findings(),
    )
    out = render(evidence)
    assert "FAIL prometheus http://nope:9090" in out
    assert "skip loki (unset)" in out
    assert "skip alertmanager (unset)" in out


def test_render_distinguishes_configured_but_silent_source():
    """An unset URL was a choice; a configured one that did not answer is not."""
    evidence = ScanEvidence(
        generated_at="now",
        agent_version="test",
        prometheus=PrometheusEvidence(reachable=True, url="http://prometheus:9090"),
        loki=LokiEvidence(reachable=False, url="http://loki:3100"),
        findings=Findings(),
    )
    out = render(evidence)
    assert "warn loki http://loki:3100 did not answer" in out


def test_render_handles_empty_evidence():
    out = render(ScanEvidence(generated_at="now", agent_version="test"))
    assert "(none identified)" in out
    assert "(no alerting rules defined)" in out
