"""Phase 3: LLM authoring behind --llm (grounding, skeletons, lint DRAFT)."""
from __future__ import annotations

import yaml

from app.draft import plan, prompt_llm, runbook_llm
from app.draft.grounding import build_allowlist, validate_prompt_profile
from app.draft.plan import DraftOptions
from app.draft.prompt_llm import PromptDraft
from app.draft.runbook_llm import DRAFT_MARKER, SkeletonDraft
from app.draft.topology import build_nodes
from app.draft.verify import StubOracle
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
from app.tools.corpus_lint import lint
from app.workspace import load as load_workspace

_SPRING_METRIC = "http_server_requests_seconds_count"


def _evidence() -> ScanEvidence:
    return ScanEvidence(
        generated_at="now",
        agent_version="test",
        prometheus=PrometheusEvidence(
            reachable=True,
            version="2.51.0",
            url="http://prometheus:9090",
            metric_count=4,
            metric_names=(
                _SPRING_METRIC,
                "http_server_requests_seconds_bucket",
                "hikaricp_connections_pending",
                "hikaricp_connections_active",
                "jvm_memory_used_bytes",
                "lettuce_command_completion_seconds_count",
                "up",
            ),
            label_names=("job", "service"),
            label_values={
                "service": ("api-gateway", "platform-service", "postgres", "redis")
            },
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
                    name="SomethingNobodyWroteARunbookFor",
                    source="prometheus",
                    severity="warning",
                    expr='up{service="platform-service"} == 0',
                    services=("platform-service",),
                ),
            ),
        ),
        loki=LokiEvidence(
            reachable=True,
            url="http://loki:3100",
            service_label="service",
            level_field="level",
            label_values={"service": ("api-gateway", "platform-service")},
            samples=(
                LogSample(
                    stream_value="platform-service",
                    line_count=2,
                    json_lines=2,
                    lines=(
                        '{"level":"ERROR","logger_name":"com.example.platform.media.X"}',
                        '{"level":"WARN","logger_name":"com.example.platform.auth.Y"}',
                    ),
                    level_values=("ERROR", "WARN"),
                    logger_names=(
                        "com.example.platform.media.X",
                        "com.example.platform.auth.Y",
                    ),
                ),
            ),
            secrets=(SecretHit("email", "email address", lines=1, matches=1),),
            log_service_hints={"postgres": ("platform-service",)},
        ),
        findings=Findings(
            services=(
                ServiceCandidate(
                    "api-gateway",
                    has_metrics=True,
                    has_logs=True,
                    kind_hints=("gateway",),
                ),
                ServiceCandidate(
                    "platform-service", has_metrics=True, has_logs=True
                ),
                ServiceCandidate(
                    "postgres",
                    has_metrics=True,
                    kind_hints=("database",),
                    log_services_hint=("platform-service",),
                ),
                ServiceCandidate("redis", has_metrics=True, kind_hints=("redis",)),
            ),
            naming_markers=(
                NamingMarker(_SPRING_METRIC, True, "Spring Boot / Micrometer"),
            ),
        ),
    )


def _spring_oracle() -> StubOracle:
    def promql_ok(expr: str) -> bool:
        if "hikaricp" in expr or "lettuce" in expr:
            return "platform-service" in expr
        return (
            _SPRING_METRIC in expr
            or expr.startswith("up{")
            or "jvm_memory_used_bytes" in expr
        )

    return StubOracle(promql_ok=promql_ok, logql_ok=lambda q: True)


def _grounded_prompt_invoke(messages):
    return PromptDraft(
        platform_description=(
            "a Spring Boot modular monolith (platform-service) behind api-gateway, "
            "with postgres and redis, observed via prometheus and loki"
        ),
        tool_run_hints=(
            "Where to run: in-network DNS (prometheus:9090, loki:3100).\n"
            "Allowlist: alert service= platform-service, api-gateway, postgres, redis.\n"
            "Hard rules: never invent prefixes.\n"
            "Golden:\n"
            '- curl -sG http://prometheus:9090/api/v1/query --data-urlencode '
            '\'query=up{service="platform-service"}\'\n'
            '- curl -sG http://loki:3100/loki/api/v1/query_range --data-urlencode '
            '\'query={service="platform-service"}\'\n'
            "- docker logs platform-service --tail 100\n"
            "Forbidden: acme-platform-service, fake-db.\n"
            "Remediation: suggest restart only as an operator step; never claim execution.\n"
        ),
    )


def _ungrounded_then_grounded():
    state = {"n": 0}

    def invoke(messages):
        state["n"] += 1
        if state["n"] == 1:
            return PromptDraft(
                platform_description="talks to invented-service-xyz only",
                tool_run_hints=(
                    'curl http://prometheus:9090/api/v1/query?query='
                    'up{service="invented-service-xyz"}'
                ),
            )
        return _grounded_prompt_invoke(messages)

    return invoke, state


def _skeleton_invoke(messages):
    return SkeletonDraft(
        meaning="Elevated errors on platform-service worth investigating.",
        first_checks=[
            'curl -sG http://loki:3100/loki/api/v1/query_range --data-urlencode '
            '\'query={service="platform-service"} |~ "(?i)error"\'',
            "Confirm the alert expression against prometheus:9090",
        ],
        common_causes=["Dependency timeout visible in platform-service logs"],
        blast_radius="Primarily platform-service; check postgres and redis neighbours.",
    )


def test_allowlist_includes_services_and_urls():
    evidence = _evidence()
    allow = build_allowlist(
        evidence,
        node_names=("platform-service",),
        extra_urls=("http://prometheus:9090", "http://loki:3100"),
    )
    assert allow.contains_name("platform-service")
    assert allow.contains_name("postgres")
    assert allow.contains_name("prometheus")
    assert "9090" in allow.ports


def test_validate_prompt_rejects_invented_service_and_remediation():
    evidence = _evidence()
    allow = build_allowlist(evidence, node_names=("platform-service",))
    failures = validate_prompt_profile(
        "uses invented-widget-42",
        'service="invented-widget-42"\nI restarted the pod',
        allow,
    )
    kinds = {f.kind for f in failures}
    assert "ungrounded" in kinds
    assert "remediation" in kinds


def test_validate_prompt_accepts_grounded_profile():
    draft = _grounded_prompt_invoke([])
    allow = build_allowlist(
        _evidence(),
        node_names=("api-gateway", "platform-service", "postgres", "redis"),
        extra_urls=("http://prometheus:9090", "http://loki:3100"),
    )
    assert validate_prompt_profile(
        draft.platform_description, draft.tool_run_hints, allow
    ) == ()


def test_author_prompt_profile_writes_grounded_file():
    evidence = _evidence()
    nodes, _ = build_nodes(evidence, _spring_oracle())
    drafted = prompt_llm.author_prompt_profile(
        evidence, nodes, preset="spring-micrometer", invoke=_grounded_prompt_invoke
    )
    data = yaml.safe_load(drafted.content)
    assert data["extends"] == "spring-micrometer"
    assert "platform-service" in data["platform_description"]
    assert all(c.accepted for c in drafted.candidates)


def test_author_prompt_retries_then_accepts():
    invoke, state = _ungrounded_then_grounded()
    evidence = _evidence()
    nodes, _ = build_nodes(evidence, _spring_oracle())
    drafted = prompt_llm.author_prompt_profile(
        evidence, nodes, preset="spring-micrometer", invoke=invoke
    )
    assert state["n"] == 2
    assert yaml.safe_load(drafted.content)["platform_description"]
    assert all(c.accepted for c in drafted.candidates)


def test_author_prompt_withholds_after_repeated_failure():
    def always_bad(messages):
        return PromptDraft(
            platform_description="depends on totally-fabricated-db",
            tool_run_hints='service="totally-fabricated-db"',
        )

    evidence = _evidence()
    nodes, _ = build_nodes(evidence, _spring_oracle())
    drafted = prompt_llm.author_prompt_profile(
        evidence, nodes, preset="spring-micrometer", invoke=always_bad
    )
    data = yaml.safe_load(drafted.content)
    assert data.get("platform_description") is None
    assert "# rejected:" in drafted.content
    assert drafted.withheld


def test_skeleton_includes_draft_marker_and_hypotheses_only():
    result = runbook_llm.draft_skeletons(
        _evidence(),
        ("SomethingNobodyWroteARunbookFor",),
        node_names=("platform-service",),
        fallback_service="platform-service",
        invoke=_skeleton_invoke,
    )
    assert result.drafted_alerts == ("SomethingNobodyWroteARunbookFor",)
    rb = result.runbooks[0]
    assert DRAFT_MARKER in rb.content
    assert "## Hypotheses-only" in rb.content
    assert "Do NOT auto-remediate" in rb.content
    assert (
        result.scenarios[0]["runbook"]
        == "runbook-something-nobody-wrote-a-runbook-for.md"
    )


def test_skeleton_falls_back_when_prose_is_ungrounded():
    def bad(messages):
        return SkeletonDraft(
            meaning="talks to invented-cache-99",
            first_checks=['service="invented-cache-99"'],
            common_causes=["nope"],
            blast_radius="invented-cache-99 only",
        )

    result = runbook_llm.draft_skeletons(
        _evidence(),
        ("SomethingNobodyWroteARunbookFor",),
        node_names=("platform-service",),
        invoke=bad,
    )
    assert DRAFT_MARKER in result.runbooks[0].content
    assert "invented-cache-99" not in result.runbooks[0].content
    assert result.candidates[0].verdict in ("rejected", "unverified")


def test_default_draft_never_calls_llm():
    calls = {"n": 0}

    def boom(messages):
        calls["n"] += 1
        raise AssertionError("LLM must not be called without --llm")

    result = plan.draft(
        _evidence(),
        DraftOptions(
            prometheus_url="http://prometheus:9090",
            use_llm=False,
            prompt_invoke=boom,
            runbook_invoke=boom,
        ),
        _spring_oracle(),
    )
    assert calls["n"] == 0
    assert not any(f.path == "prompt_profile.yaml" for f in result.files)
    assert result.draft_runbooks == ()
    assert "SomethingNobodyWroteARunbookFor" in result.uncovered_alerts


def test_llm_draft_adds_prompt_and_skeletons():
    result = plan.draft(
        _evidence(),
        DraftOptions(
            prometheus_url="http://prometheus:9090",
            loki_url="http://loki:3100",
            use_llm=True,
            prompt_invoke=_grounded_prompt_invoke,
            runbook_invoke=_skeleton_invoke,
        ),
        _spring_oracle(),
    )
    paths = {f.path for f in result.files}
    assert "prompt_profile.yaml" in paths
    assert any(
        p.endswith("runbook-something-nobody-wrote-a-runbook-for.md") for p in paths
    )
    assert result.draft_runbooks == ("SomethingNobodyWroteARunbookFor",)
    assert "SomethingNobodyWroteARunbookFor" not in result.uncovered_alerts

    scenarios = yaml.safe_load(
        next(f for f in result.files if f.path == "scenarios.yaml").content
    )
    runbooks = {s["runbook"] for s in scenarios["scenarios"]}
    assert "runbook-something-nobody-wrote-a-runbook-for.md" in runbooks


def test_llm_prompt_only_skips_skeletons():
    result = plan.draft(
        _evidence(),
        DraftOptions(
            prometheus_url="http://prometheus:9090",
            use_llm=True,
            llm_prompt=True,
            llm_runbooks=False,
            prompt_invoke=_grounded_prompt_invoke,
            runbook_invoke=lambda m: (_ for _ in ()).throw(AssertionError("no")),
        ),
        _spring_oracle(),
    )
    assert any(f.path == "prompt_profile.yaml" for f in result.files)
    assert result.draft_runbooks == ()
    assert "SomethingNobodyWroteARunbookFor" in result.uncovered_alerts


def test_lint_rejects_draft_marker_then_passes_when_removed(tmp_path):
    result = plan.draft(
        _evidence(),
        DraftOptions(
            prometheus_url="http://prometheus:9090",
            loki_url="http://loki:3100",
            use_llm=True,
            prompt_invoke=_grounded_prompt_invoke,
            runbook_invoke=_skeleton_invoke,
        ),
        _spring_oracle(),
    )
    for drafted in result.files:
        path = tmp_path / drafted.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(drafted.content, encoding="utf-8", newline="\n")

    failed = lint(load_workspace(str(tmp_path)))
    assert not failed.ok
    assert any("DRAFT" in e for e in failed.errors)

    for path in (tmp_path / "runbooks").glob("runbook-*.md"):
        text = path.read_text(encoding="utf-8")
        if DRAFT_MARKER in text:
            path.write_text(text.replace(DRAFT_MARKER + "\n", ""), encoding="utf-8")

    cleared = lint(load_workspace(str(tmp_path)))
    assert cleared.ok, cleared.errors


def test_llm_draft_passes_validate(tmp_path, capsys):
    from app.cli import main

    result = plan.draft(
        _evidence(),
        DraftOptions(
            prometheus_url="http://prometheus:9090",
            loki_url="http://loki:3100",
            use_llm=True,
            prompt_invoke=_grounded_prompt_invoke,
            runbook_invoke=_skeleton_invoke,
        ),
        _spring_oracle(),
    )
    for drafted in result.files:
        path = tmp_path / drafted.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(drafted.content, encoding="utf-8", newline="\n")

    assert main(["validate", "-w", str(tmp_path)]) == 0
    assert "validate OK" in capsys.readouterr().out


def test_report_lists_draft_runbooks():
    result = plan.draft(
        _evidence(),
        DraftOptions(
            prometheus_url="http://prometheus:9090",
            use_llm=True,
            prompt_invoke=_grounded_prompt_invoke,
            runbook_invoke=_skeleton_invoke,
        ),
        _spring_oracle(),
    )
    text = plan.report(result, _evidence())
    assert "DRAFT runbooks" in text
    assert "SomethingNobodyWroteARunbookFor" in text
