"""Deterministic workspace drafting: the oracle, each generator, and the writer."""
from __future__ import annotations

import json

import httpx
import pytest
import yaml

from app.draft import plan
from app.draft import profiles, redaction, topology
from app.draft.alerts import _tokens, draft_scenarios, pair_alerts
from app.draft.models import REJECTED, UNVERIFIED, VERIFIED, Candidate
from app.draft.plan import DraftOptions
from app.draft.verify import LiveOracle, StubOracle, captures_group, matches_lines
from app.scan.models import (
    AlertRule,
    Findings,
    LogSample,
    LokiEvidence,
    NamingMarker,
    PrometheusEvidence,
    ScanEvidence,
    ServiceCandidate,
)
from app.scan.scrub import SecretHit

_SPRING_METRIC = "http_server_requests_seconds_count"


def _evidence(**overrides) -> ScanEvidence:
    """A Spring-shaped stack: gateway, monolith, postgres, redis."""
    prometheus = PrometheusEvidence(
        reachable=True,
        version="2.51.0",
        metric_count=4,
        metric_names=(
            _SPRING_METRIC,
            "http_server_requests_seconds_bucket",
            "hikaricp_connections_pending",
            "hikaricp_connections_active",
            "jvm_memory_used_bytes",
            "jvm_memory_max_bytes",
            "lettuce_command_completion_seconds_count",
            "up",
        ),
        label_names=("job", "service"),
        label_values={"service": ("api-gateway", "platform-service", "postgres", "redis")},
        rules=(
            AlertRule(
                name="HighErrorRate",
                source="prometheus",
                severity="critical",
                expr=f'rate({_SPRING_METRIC}{{service="platform-service"}}[5m]) > 1',
                runbook="runbook-high-error-rate.md",
                services=("platform-service",),
            ),
            AlertRule(
                name="HikariPoolExhaustion",
                source="prometheus",
                severity="critical",
                expr='hikaricp_connections_pending{service="platform-service"} > 5',
                services=("platform-service",),
            ),
            AlertRule(
                name="SomethingNobodyWroteARunbookFor",
                source="prometheus",
                severity="warning",
            ),
        ),
    )
    loki = LokiEvidence(
        reachable=True,
        service_label="service",
        level_field="level",
        label_values={"service": ("api-gateway", "platform-service")},
        samples=(
            LogSample(
                stream_value="platform-service",
                line_count=10,
                json_lines=10,
                lines=(
                    '{"level":"ERROR","logger_name":"com.example.platform.media.'
                    'MediaService","message":"upload failed"}',
                    '{"level":"WARN","logger_name":"com.example.platform.auth.'
                    'TokenFilter","message":"jwt expired"}',
                ),
                level_values=("ERROR", "INFO", "WARN"),
                logger_names=(
                    "com.example.platform.media.MediaService",
                    "com.example.platform.auth.TokenFilter",
                ),
            ),
        ),
        rules=(
            AlertRule(
                name="PostgresErrorsInLogs",
                source="loki",
                severity="warning",
                line_filters=("(?i)(postgres|jdbc).*(refused|timeout)",),
            ),
        ),
        secrets=(
            SecretHit("email", "email address", lines=4, matches=5),
            SecretHit("uuid", "UUID (often a tenant, user, or request id)", lines=9, matches=20),
        ),
        log_service_hints={"postgres": ("platform-service",)},
    )
    findings = Findings(
        services=(
            ServiceCandidate("api-gateway", has_metrics=True, has_logs=True, kind_hints=("gateway",)),
            ServiceCandidate("platform-service", has_metrics=True, has_logs=True),
            ServiceCandidate(
                "postgres",
                has_metrics=True,
                kind_hints=("database",),
                log_services_hint=("platform-service",),
            ),
            ServiceCandidate("redis", has_metrics=True, kind_hints=("redis",)),
        ),
        naming_markers=(
            NamingMarker(_SPRING_METRIC, True, "Spring Boot Actuator / Micrometer"),
            NamingMarker("http_requests_total", False, "community naming"),
        ),
    )
    base = {
        "prometheus": prometheus,
        "loki": loki,
        "findings": findings,
    }
    base.update(overrides)
    return ScanEvidence(generated_at="now", agent_version="test", **base)


def _spring_oracle() -> StubOracle:
    """Behaves like a real Spring stack: only the monolith has client metrics.

    Being service-aware matters — an oracle that says yes to `hikaricp_*` for
    every service would invent a database edge from the gateway too.
    """

    def promql_ok(expr: str) -> bool:
        if "hikaricp" in expr or "lettuce" in expr:
            return "platform-service" in expr
        return (
            _SPRING_METRIC in expr
            or expr.startswith("up{")
            or "jvm_memory_used_bytes" in expr
        )

    return StubOracle(promql_ok=promql_ok, logql_ok=lambda q: True)


# -- oracle ------------------------------------------------------------------
def test_captures_group_requires_a_capture_and_enough_hits():
    lines = ["c.p.media.X boom", "c.p.auth.Y boom", "no logger here"]
    ok, detail = captures_group(r"c\.p\.([a-z]+)", lines)
    assert ok
    assert "2/3" in detail

    ok, detail = captures_group(r"c\.p\.[a-z]+", lines)
    assert not ok
    assert "no capture group" in detail

    ok, detail = captures_group(r"(zzz)", lines)
    assert not ok


def test_captures_group_rejects_invalid_regex():
    ok, detail = captures_group(r"([a-z", ["x"])
    assert not ok
    assert "invalid regex" in detail


def test_matches_lines_counts_hits():
    ok, detail = matches_lines("(?i)postgres", ["PostgreSQL down", "fine"])
    assert ok
    assert "1/2" in detail
    assert matches_lines("(?i)postgres", [])[0] is False


def test_live_oracle_reports_series_and_lines(monkeypatch):
    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, **kwargs):
            if "/loki/" in url:
                return _Resp(
                    {
                        "data": {
                            "result": [
                                {"stream": {"service": "app"}, "values": [["1", "boom"]]}
                            ]
                        }
                    }
                )
            return _Resp(
                {"data": {"result": [{"metric": {"client": "a", "server": "b"}}]}}
            )

    monkeypatch.setattr(httpx, "Client", _Client)
    oracle = LiveOracle("http://prometheus:9090", "http://loki:3100")

    ok, detail = oracle.promql("up")
    assert ok and "1 series" in detail
    assert oracle.label_pairs("x", "client", "server") == (("a", "b"),)

    ok, detail = oracle.logql('{service="app"}')
    assert ok and "line(s)" in detail


def test_live_oracle_without_loki_rejects_log_checks():
    oracle = LiveOracle("http://prometheus:9090")
    ok, detail = oracle.logql('{service="app"}')
    assert not ok
    assert "no Loki configured" in detail


# -- preset scoring ----------------------------------------------------------
def test_probe_target_prefers_an_application_over_a_datastore():
    target = profiles.probe_target(_evidence())
    assert target is not None
    assert target.service in ("api-gateway", "platform-service")
    assert target.metric_label == "service"


def test_probe_target_uses_job_when_there_is_no_service_label():
    evidence = _evidence(
        prometheus=PrometheusEvidence(
            reachable=True, label_values={"job": ("app",)}, metric_names=("up",)
        )
    )
    target = profiles.probe_target(evidence)
    assert target.metric_label == "job"
    assert target.service == "app"


def test_score_presets_measures_which_preset_returns_data():
    evidence = _evidence()
    target = profiles.probe_target(evidence)
    scores = profiles.score_presets(evidence, _spring_oracle(), target)

    by_name = {s.name: s for s in scores}
    assert by_name["spring-micrometer"].verified > by_name["generic-prometheus"].verified
    assert profiles.choose_preset(scores) == "spring-micrometer"


def test_choose_preset_falls_back_when_nothing_returns_data():
    evidence = _evidence()
    target = profiles.probe_target(evidence)
    scores = profiles.score_presets(evidence, StubOracle(), target)
    assert all(s.verified == 0 for s in scores)
    assert profiles.choose_preset(scores) == "generic-prometheus"


# -- metrics profile ---------------------------------------------------------
def test_metrics_profile_extends_the_measured_preset():
    evidence = _evidence()
    target = profiles.probe_target(evidence)
    oracle = _spring_oracle()
    scores = profiles.score_presets(evidence, oracle, target)
    drafted = profiles.draft_metrics_profile(
        evidence,
        oracle,
        preset="spring-micrometer",
        target=target,
        kinds=("gateway", "database", "redis"),
        scores=scores,
    )
    data = yaml.safe_load(drafted.content)
    assert data["extends"] == "spring-micrometer"
    assert data["dependency_probes"]["database"].startswith("hikaricp_connections_pending")
    assert data["dependency_probes"]["redis"].startswith("lettuce_command")
    assert "# Generated by diag draft" in drafted.content
    assert "probe service" in drafted.content


def test_metrics_profile_retargets_templates_at_the_observed_label():
    """A stack labelling by job needs every template rewritten, and verified."""
    evidence = _evidence(
        prometheus=PrometheusEvidence(
            reachable=True,
            metric_names=(_SPRING_METRIC,),
            label_values={"job": ("platform-service",)},
        )
    )
    target = profiles.probe_target(evidence)
    oracle = StubOracle(promql_ok=lambda expr: 'job="platform-service"' in expr)
    drafted = profiles.draft_metrics_profile(
        evidence, oracle, preset="spring-micrometer", target=target
    )
    data = yaml.safe_load(drafted.content)
    assert 'job="{service}"' in data["templates"]["error_rate"]
    assert 'service="' not in data["templates"]["error_rate"]
    assert all(c.accepted for c in drafted.candidates)


def test_metrics_profile_comments_out_templates_that_return_nothing():
    evidence = _evidence()
    target = profiles.probe_target(evidence)
    # Only service_up works; the rest of the suite returns nothing.
    oracle = StubOracle(promql_ok=lambda expr: expr.startswith("up{"))
    drafted = profiles.draft_metrics_profile(
        evidence, oracle, preset="spring-micrometer", target=target
    )
    data = yaml.safe_load(drafted.content)
    assert "templates" not in data, "a withheld-only section must not appear as a key"
    assert "# rejected: query returned no data" in drafted.content
    assert "# WITHHELD" in drafted.content
    assert {c.key for c in drafted.withheld} >= {"error_rate", "latency_p99"}


def test_withheld_templates_never_null_out_the_preset_suite(tmp_path):
    """`templates:` holding only comments parses as null and would erase the
    preset's whole metric suite. The preset must survive untouched."""
    from app.profile import build_profile

    evidence = _evidence()
    target = profiles.probe_target(evidence)
    drafted = profiles.draft_metrics_profile(
        evidence, StubOracle(), preset="spring-micrometer", target=target
    )
    (tmp_path / "metrics_profile.yaml").write_text(drafted.content, encoding="utf-8")

    profile = build_profile(profile_dir=tmp_path)
    assert profile.load_errors == ()
    query = profile.metrics.render("error_rate", service="platform-service")
    assert query and _SPRING_METRIC in query


def test_metrics_profile_without_probe_service_is_still_valid():
    drafted = profiles.draft_metrics_profile(
        _evidence(), StubOracle(), preset="generic-prometheus", target=None
    )
    assert yaml.safe_load(drafted.content)["extends"] == "generic-prometheus"


# -- logs profile ------------------------------------------------------------
def test_logs_profile_is_measured_from_real_streams():
    drafted = profiles.draft_logs_profile(_evidence(), _spring_oracle())
    data = yaml.safe_load(drafted.content)

    assert data["service_label"] == "service"
    assert data["use_json_parser"] is True
    # INFO is dropped: retrieving it would drown the sample.
    assert data["level_filter"] == "ERROR|WARN"
    assert data["module_regex"] == r"com\.example\.platform\.([a-z0-9_]+)"
    assert (
        data["alert_line_filters"]["PostgresErrorsInLogs"]
        == "(?i)(postgres|jdbc).*(refused|timeout)"
    )


def test_logs_profile_module_regex_needs_two_logger_names():
    evidence = _evidence()
    single = LogSample(
        stream_value="app",
        line_count=1,
        json_lines=1,
        lines=("{}",),
        logger_names=("com.example.OnlyOne",),
    )
    evidence = _evidence(
        loki=LokiEvidence(
            reachable=True, service_label="service", samples=(single,)
        )
    )
    drafted = profiles.draft_logs_profile(evidence, _spring_oracle())
    assert "module_regex" not in yaml.safe_load(drafted.content)


def test_logs_profile_flags_a_line_filter_that_matches_nothing():
    oracle = StubOracle(logql_ok=lambda q: "|~" not in q)
    drafted = profiles.draft_logs_profile(_evidence(), oracle)
    data = yaml.safe_load(drafted.content)
    assert "alert_line_filters" not in data or not data["alert_line_filters"]
    assert "# rejected: no lines in the last 60m" in drafted.content


def test_logs_profile_without_a_service_label_falls_back_and_says_so():
    evidence = _evidence(loki=LokiEvidence(reachable=True, service_label=""))
    drafted = profiles.draft_logs_profile(evidence, StubOracle())
    assert "# unverified: fell back to the preset default" in drafted.content


# -- topology ----------------------------------------------------------------
def test_topology_nodes_carry_kind_and_log_redirect():
    nodes, candidates = topology.build_nodes(_evidence(), _spring_oracle())
    by_name = {n.name: n for n in nodes}

    assert by_name["postgres"].kind == "database"
    assert by_name["postgres"].log_services == ["platform-service"]
    assert by_name["platform-service"].kind == "http"
    assert by_name["api-gateway"].kind == "gateway"
    assert any(c.key.endswith("log_services") for c in candidates)


def test_topology_edges_come_from_client_metrics():
    nodes, _ = topology.build_nodes(_evidence(), _spring_oracle())
    by_name = {n.name: n for n in nodes}
    assert "postgres" in by_name["platform-service"].downstream
    assert "redis" in by_name["platform-service"].downstream
    assert by_name["postgres"].upstream == ["platform-service"]


def test_topology_withholds_edges_with_no_client_metrics():
    """No hikaricp/lettuce metrics means no proof of the edge."""
    nodes, candidates = topology.build_nodes(_evidence(), StubOracle())
    by_name = {n.name: n for n in nodes}
    assert by_name["platform-service"].downstream == []
    withheld = [c for c in candidates if c.verdict == REJECTED]
    assert any("postgres" == c.value for c in withheld)


def test_topology_prefers_the_tracing_service_graph():
    evidence = _evidence()
    evidence = _evidence(
        prometheus=PrometheusEvidence(
            reachable=True,
            metric_names=evidence.prometheus.metric_names
            + ("traces_service_graph_request_total",),
            label_values=evidence.prometheus.label_values,
        )
    )
    oracle = StubOracle(
        pairs={
            "traces_service_graph_request_total": [
                ("api-gateway", "platform-service"),
                ("platform-service", "postgres"),
            ]
        }
    )
    nodes, candidates = topology.build_nodes(evidence, oracle)
    by_name = {n.name: n for n in nodes}
    assert by_name["api-gateway"].downstream == ["platform-service"]
    assert by_name["platform-service"].downstream == ["postgres"]
    assert any("service graph" in c.detail for c in candidates)


def test_topology_gateway_edge_is_proposed_but_not_asserted():
    nodes, candidates = topology.build_nodes(_evidence(), _spring_oracle())
    rendered = topology.render_service_map(nodes, candidates, _evidence())
    unverified = [c for c in candidates if c.verdict == UNVERIFIED]
    assert any(c.key.startswith("services.api-gateway") for c in unverified)
    assert "downstream candidates not written" in rendered.content
    assert "- platform-service  (no signal proves this edge)" in rendered.content
    # One comment block, so uncommenting cannot produce a duplicate mapping key.
    assert rendered.content.count("# downstream:") == 0


def test_service_map_parses_and_loads_as_a_dependency_map(tmp_path):
    from app.dependency_map import DependencyMap

    nodes, candidates = topology.build_nodes(_evidence(), _spring_oracle())
    rendered = topology.render_service_map(nodes, candidates, _evidence())
    path = tmp_path / "service_map.yaml"
    path.write_text(rendered.content, encoding="utf-8")

    dep_map = DependencyMap.load(str(path))
    assert set(dep_map.known_services()) == {
        "api-gateway",
        "platform-service",
        "postgres",
        "redis",
    }
    assert dep_map.kind("postgres") == "database"
    assert dep_map.log_services("postgres") == ["platform-service"]
    assert dep_map.blast_radius("platform-service") == ["postgres", "redis"]


# -- alerts and runbooks -----------------------------------------------------
def test_tokens_splits_camel_case():
    assert _tokens("HighErrorRate") == {"high", "error", "rate"}
    assert _tokens("runbook-postgres-connectivity") == {
        "runbook",
        "postgres",
        "connectivity",
    }


def test_pair_alerts_prefers_the_rule_annotation():
    rules = (
        AlertRule(name="Whatever", source="prometheus", runbook="runbook-high-error-rate.md"),
    )
    pairings, uncovered = pair_alerts(rules, ("runbook-high-error-rate.md",))
    assert pairings[0].runbook == "runbook-high-error-rate.md"
    assert "annotation" in pairings[0].how
    assert uncovered == ()


def test_pair_alerts_uses_the_reference_index_then_name_overlap():
    corpus = (
        "runbook-postgres-connectivity.md",
        "runbook-db-pool-exhaustion.md",
        "runbook-high-error-rate.md",
    )
    rules = (
        # In the shipped reference scenarios by alert name.
        AlertRule(name="PostgresErrorsInLogs", source="loki"),
        # Only reachable by token overlap on "pool"/"exhaustion".
        AlertRule(name="HikariPoolExhaustion", source="prometheus"),
        AlertRule(name="TotallyUnrelatedAlert", source="prometheus"),
    )
    pairings, uncovered = pair_alerts(rules, corpus)
    paired = {p.alert.name: p for p in pairings}
    assert paired["PostgresErrorsInLogs"].runbook == "runbook-postgres-connectivity.md"
    assert paired["HikariPoolExhaustion"].runbook == "runbook-db-pool-exhaustion.md"
    assert uncovered == ("TotallyUnrelatedAlert",)


def test_pair_alerts_reports_everything_uncovered_without_a_corpus():
    rules = (AlertRule(name="HighErrorRate", source="prometheus"),)
    pairings, uncovered = pair_alerts(rules, ())
    assert pairings == ()
    assert uncovered == ("HighErrorRate",)


def test_draft_scenarios_pairs_runbooks_as_a_unit():
    result = draft_scenarios(
        _evidence(), node_names=("platform-service",), fallback_service="platform-service"
    )
    data = yaml.safe_load(result.scenarios.content)
    names = {s["labels"]["alertname"] for s in data["scenarios"]}
    assert "HighErrorRate" in names
    assert "SomethingNobodyWroteARunbookFor" in result.uncovered

    # Every scenario's runbook is carried into the draft: lint needs both sides.
    referenced = {s["runbook"] for s in data["scenarios"]}
    carried = {f.path.removeprefix("runbooks/") for f in result.runbooks}
    assert referenced == carried
    assert {c.path for c in result.copied} == {
        f"runbooks/{name}" for name in referenced
    }
    assert result.unused, "unused reference runbooks should be reported, not copied"


def test_draft_scenarios_uses_severity_and_service_from_the_rule():
    result = draft_scenarios(_evidence(), node_names=("platform-service",))
    data = yaml.safe_load(result.scenarios.content)
    by_alert = {s["labels"]["alertname"]: s for s in data["scenarios"]}
    assert by_alert["HighErrorRate"]["labels"]["severity"] == "critical"
    assert by_alert["HighErrorRate"]["labels"]["service"] == "platform-service"


# -- redaction ---------------------------------------------------------------
def test_redaction_proposes_only_what_matched():
    drafted = redaction.draft_redaction(_evidence(), preset="spring-micrometer")
    data = yaml.safe_load(drafted.content)
    names = {rule["name"] for rule in data["rules"]}

    assert "email" in names
    # UUIDs match trace ids too, so the rule is proposed commented out.
    assert "uuid" not in names
    assert "# unverified:" in drafted.content
    assert "high false-positive risk" in drafted.content
    assert "matched 5 time(s) on 4 sampled line(s)" in drafted.content


def test_redaction_rules_compile_and_scrub():
    import re

    drafted = redaction.draft_redaction(_evidence(), preset="generic-prometheus")
    rules = yaml.safe_load(drafted.content)["rules"]
    assert rules
    for rule in rules:
        compiled = re.compile(rule["pattern"], re.IGNORECASE)
        assert compiled.sub(rule["replacement"], "ops@example.com") is not None


def test_redaction_without_samples_says_nothing_could_be_proposed():
    evidence = _evidence(loki=LokiEvidence(reachable=True))
    drafted = redaction.draft_redaction(evidence, preset="generic-prometheus")
    assert "no log lines were sampled" in drafted.content
    assert "rules" not in yaml.safe_load(drafted.content)


def test_redaction_never_leaves_the_agent_with_zero_rules(tmp_path):
    """`rules:` holding only comments parses as null, which would wipe the
    preset's baseline scrubbing and stop the agent from starting."""
    from app.profile import build_profile

    # Only high-false-positive patterns matched, so nothing is proposed active.
    evidence = _evidence(
        loki=LokiEvidence(
            reachable=True,
            samples=(LogSample(stream_value="app", line_count=1, json_lines=1),),
            secrets=(SecretHit("uuid", "UUID", lines=3, matches=3),),
        )
    )
    drafted = redaction.draft_redaction(evidence, preset="generic-prometheus")
    assert "rules" not in yaml.safe_load(drafted.content)
    (tmp_path / "redaction.yaml").write_text(drafted.content, encoding="utf-8")

    profile = build_profile(profile_dir=tmp_path)
    assert profile.redaction.rules, "preset scrubbing must survive a withheld-only draft"


# -- plan --------------------------------------------------------------------
def _options(**kwargs) -> DraftOptions:
    base = {"prometheus_url": "http://prometheus:9090", "loki_url": "http://loki:3100"}
    base.update(kwargs)
    return DraftOptions(**base)


def test_draft_produces_every_expected_file():
    result = plan.draft(_evidence(), _options(), _spring_oracle())
    paths = {f.path for f in result.files}
    assert {
        "agent.yaml",
        "service_map.yaml",
        "metrics_profile.yaml",
        "logs_profile.yaml",
        "redaction.yaml",
        "scenarios.yaml",
    } <= paths
    assert any(p.startswith("runbooks/") for p in paths)
    assert result.preset == "spring-micrometer"
    assert result.uncovered_alerts == ("SomethingNobodyWroteARunbookFor",)


def test_draft_manifest_only_declares_paths_that_exist():
    """A declared-but-missing path is a hard workspace error."""
    evidence = _evidence(
        prometheus=PrometheusEvidence(
            reachable=True,
            metric_names=(_SPRING_METRIC,),
            label_values={"service": ("app",)},
        ),
        loki=LokiEvidence(reachable=True, service_label="service"),
    )
    result = plan.draft(evidence, _options(), _spring_oracle())
    manifest = next(f for f in result.files if f.path == "agent.yaml")
    data = yaml.safe_load(manifest.content)
    assert "scenarios" not in data
    assert "runbooks" not in data
    assert any("no alerting rules" in w for w in result.warnings)


def test_draft_warns_when_no_preset_verifies():
    result = plan.draft(_evidence(), _options(), StubOracle())
    assert result.preset == "generic-prometheus"
    assert any("no preset" in w for w in result.warnings)


def test_report_lists_withheld_values_and_the_backlog():
    result = plan.draft(_evidence(), _options(), _spring_oracle())
    text = plan.report(result, _evidence())

    assert "preset scoring" in text
    assert "spring-micrometer" in text
    assert "alerts with no runbook" in text
    assert "SomethingNobodyWroteARunbookFor" in text
    assert "runbooks carried over" in text
    assert text.isascii()


def test_draft_result_serialises():
    result = plan.draft(_evidence(), _options(), _spring_oracle())
    payload = json.loads(json.dumps(result.to_dict()))
    assert payload["preset"] == "spring-micrometer"
    assert payload["preset_scores"][0]["name"] == "spring-micrometer"


# -- the acceptance criterion ------------------------------------------------
def test_drafted_workspace_passes_validate_and_lint(tmp_path, capsys):
    """The whole point: a drafted workspace must satisfy the existing gates."""
    from app.cli import main

    result = plan.draft(_evidence(), _options(), _spring_oracle())
    for drafted in result.files:
        path = tmp_path / drafted.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(drafted.content, encoding="utf-8", newline="\n")

    assert main(["validate", "-w", str(tmp_path)]) == 0
    assert main(["lint", "-w", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    assert "validate OK" in out
    assert "corpus lint OK" in out
    assert "services=4" in out


def test_drafted_profile_resolves_the_measured_preset(tmp_path):
    from app.profile import build_profile

    result = plan.draft(_evidence(), _options(), _spring_oracle())
    for drafted in result.files:
        path = tmp_path / drafted.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(drafted.content, encoding="utf-8", newline="\n")

    profile = build_profile(profile_dir=tmp_path)
    assert profile.load_errors == ()
    assert profile.logs.service_label == "service"
    assert profile.logs.module_regex == r"com\.example\.platform\.([a-z0-9_]+)"
    assert "PostgresErrorsInLogs" in profile.logs.alert_line_filters
    # Rendering a template proves the preset chain resolved.
    query = profile.metrics.render("error_rate", service="platform-service")
    assert _SPRING_METRIC in query
    assert {r.name for r in profile.redaction.rules} >= {"email"}


# -- writing -----------------------------------------------------------------
def test_write_refuses_to_clobber_then_obeys_force(tmp_path):
    from app.draft.cli import _write

    result = plan.draft(_evidence(), _options(), _spring_oracle())
    written, blocked = _write(result, tmp_path, dry_run=False, force=False)
    assert blocked == []
    assert (tmp_path / "service_map.yaml").is_file()

    _written2, blocked2 = _write(result, tmp_path, dry_run=False, force=False)
    assert blocked2, "existing files must block a second write"
    assert (tmp_path / "service_map.yaml").is_file()

    _written3, blocked3 = _write(result, tmp_path, dry_run=False, force=True)
    assert blocked3 == []


def test_write_dry_run_touches_nothing(tmp_path):
    from app.draft.cli import _write

    result = plan.draft(_evidence(), _options(), _spring_oracle())
    written, blocked = _write(result, tmp_path, dry_run=True, force=False)
    assert written and blocked == []
    assert list(tmp_path.iterdir()) == []


# -- bundle round trip -------------------------------------------------------
def test_evidence_survives_a_bundle_round_trip():
    original = _evidence()
    restored = ScanEvidence.from_dict(json.loads(json.dumps(original.to_dict())))

    assert restored.prometheus.label_values == original.prometheus.label_values
    assert restored.loki.log_service_hints == original.loki.log_service_hints
    assert restored.loki.samples[0].logger_names == original.loki.samples[0].logger_names
    assert [r.name for r in restored.all_rules()] == [
        r.name for r in original.all_rules()
    ]
    assert restored.findings.services == original.findings.services
    assert restored.loki.secrets == original.loki.secrets


def test_drafting_from_a_restored_bundle_matches_a_live_draft():
    original = _evidence()
    restored = ScanEvidence.from_dict(json.loads(json.dumps(original.to_dict())))
    live = plan.draft(original, _options(), _spring_oracle())
    from_bundle = plan.draft(restored, _options(), _spring_oracle())
    assert [f.content for f in from_bundle.files] == [f.content for f in live.files]


@pytest.mark.parametrize(
    "payload,message",
    [
        ({}, "no integer 'schema'"),
        ({"schema": 99}, "newer than this agent supports"),
    ],
)
def test_bundle_rejects_unusable_payloads(payload, message):
    from app.scan.models import BundleError

    with pytest.raises(BundleError) as exc:
        ScanEvidence.from_dict(payload)
    assert message in str(exc.value)


def test_candidate_reason_includes_detail():
    assert Candidate("k", "v", "why", VERIFIED).reason() == "verified"
    assert (
        Candidate("k", "v", "why", REJECTED, "no data").reason() == "rejected: no data"
    )


# -- the command -------------------------------------------------------------
# `fake_stack` is the same fixture the scan tests use, so both commands run
# against one definition of what a stack looks like.
def test_draft_command_scans_writes_and_points_at_the_gates(tmp_path, fake_stack, capsys):
    from app.cli import main

    fake_stack()
    out = tmp_path / "staged"
    assert main(
        [
            "draft",
            "--prometheus-url",
            "http://prometheus:9090",
            "--loki-url",
            "http://loki:3100",
            "--out",
            str(out),
        ]
    ) == 0

    printed = capsys.readouterr().out
    assert "preset scoring" in printed
    assert "diag validate -w" in printed
    assert (out / "service_map.yaml").is_file()
    assert (out / "agent.yaml").is_file()
    assert yaml.safe_load((out / "logs_profile.yaml").read_text(encoding="utf-8"))


def test_draft_command_refuses_to_clobber_without_force(tmp_path, fake_stack, capsys):
    from app.cli import main

    fake_stack()
    args = [
        "draft",
        "--prometheus-url",
        "http://prometheus:9090",
        "--loki-url",
        "http://loki:3100",
        "--out",
        str(tmp_path / "staged"),
    ]
    assert main(args) == 0
    capsys.readouterr()

    assert main(args) == 1
    err = capsys.readouterr().err
    assert "already exist" in err
    assert "--force" in err

    assert main([*args, "--force"]) == 0


def test_draft_command_dry_run_writes_nothing(tmp_path, fake_stack, capsys):
    from app.cli import main

    fake_stack()
    out = tmp_path / "staged"
    assert main(
        [
            "draft",
            "--prometheus-url",
            "http://prometheus:9090",
            "--out",
            str(out),
            "--dry-run",
        ]
    ) == 0
    assert "dry run" in capsys.readouterr().out
    assert not out.exists()


def test_draft_command_fails_when_nothing_can_be_verified(tmp_path, monkeypatch, capsys):
    """Without Prometheus there is no oracle, so drafting would be guessing."""
    from app.cli import main

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, *args, **kwargs):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "Client", _Client)
    assert main(
        ["draft", "--prometheus-url", "http://nope:9090", "--out", str(tmp_path / "x")]
    ) == 2
    assert "unreachable" in capsys.readouterr().err


def test_draft_command_reads_a_bundle(tmp_path, fake_stack, capsys):
    from app.cli import main

    bundle = tmp_path / "scan.json"
    bundle.write_text(json.dumps(_evidence().to_dict()), encoding="utf-8")
    fake_stack()

    out = tmp_path / "staged"
    assert main(
        [
            "draft",
            "--bundle",
            str(bundle),
            "--prometheus-url",
            "http://prometheus:9090",
            "--out",
            str(out),
        ]
    ) == 0
    assert (out / "scenarios.yaml").is_file()


def test_draft_command_json_output_is_pipeable(tmp_path, fake_stack, capsys):
    """Progress goes to stderr so stdout stays a single JSON document."""
    from app.cli import main

    fake_stack()
    assert main(
        [
            "draft",
            "--prometheus-url",
            "http://prometheus:9090",
            "--out",
            str(tmp_path / "staged"),
            "--json",
        ]
    ) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["preset"]
    assert "wrote" in captured.err


def test_draft_command_rejects_an_unreadable_bundle(tmp_path, capsys):
    from app.cli import main

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert main(["draft", "--bundle", str(bad), "--out", str(tmp_path / "x")]) == 2
    assert "cannot read bundle" in capsys.readouterr().err
