"""Unit tests for diag install discovery / collect / generate / verify."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from app.install.collect import collect
from app.install.discover import (
    _build_reachability,
    _match_image,
    _parse_published_port,
    _tools_from_containers,
)
from app.install.generate import generate
from app.install.models import (
    AddressingMode,
    DiscoveryReport,
    ReachabilityMatrix,
    ToolEndpoint,
    ToolKind,
)
from app.install.verify import verify


def test_match_image_hints():
    assert _match_image("prom/prometheus:v2.52.0") == ToolKind.PROMETHEUS
    assert _match_image("grafana/loki:3.0.0") == ToolKind.LOKI
    assert _match_image("grafana/grafana:11.0.0") == ToolKind.GRAFANA
    assert _match_image("prom/alertmanager:v0.27.0") == ToolKind.ALERTMANAGER
    assert _match_image("axllent/mailpit:latest") == ToolKind.MAILPIT
    assert _match_image("nginx:latest") is None


def test_parse_published_port_prefers_container_port():
    ports = "0.0.0.0:9090->9090/tcp, 0.0.0.0:9091->9091/tcp"
    assert _parse_published_port(ports, 9090) == 9090
    assert _parse_published_port("127.0.0.1:3000->3000/tcp", 3000) == 3000
    assert _parse_published_port("", 9090) is None


def test_parse_published_port_prefers_mailpit_http_ui():
    ports = "0.0.0.0:1025->1025/tcp, 0.0.0.0:8025->8025/tcp"
    assert _parse_published_port(ports, 8025) == 8025
    assert _parse_published_port(ports, 1025) == 1025


def test_tools_from_containers_dedupes():
    containers = [
        {
            "Image": "prom/prometheus:v2.52.0",
            "Names": "/prometheus",
            "Ports": "0.0.0.0:9090->9090/tcp",
        },
        {
            "Image": "prom/prometheus:v2.52.0",
            "Names": "/prometheus-backup",
            "Ports": "0.0.0.0:19090->9090/tcp",
        },
        {
            "Image": "grafana/loki:3.0.0",
            "Names": "loki",
            "Ports": "0.0.0.0:3100->3100/tcp",
        },
    ]
    tools = _tools_from_containers(containers)
    kinds = [t.kind for t in tools]
    assert kinds.count(ToolKind.PROMETHEUS) == 1
    assert ToolKind.LOKI in kinds
    prom = next(t for t in tools if t.kind == ToolKind.PROMETHEUS)
    assert prom.published_port == 9090
    assert prom.container_name == "prometheus"


def test_reachability_same_docker_network():
    report = DiscoveryReport(target="local")
    report.tools = [
        ToolEndpoint(
            kind=ToolKind.PROMETHEUS,
            reachable=True,
            url="http://prometheus:9090",
            addressing_mode=AddressingMode.DOCKER_DNS,
            container_name="prometheus",
            docker_network="obs_net",
        ),
        ToolEndpoint(
            kind=ToolKind.LOKI,
            reachable=True,
            url="http://loki:3100",
            addressing_mode=AddressingMode.DOCKER_DNS,
            container_name="loki",
            docker_network="obs_net",
        ),
    ]
    matrix = _build_reachability(report, target="local")
    assert matrix.agent_placement == "same_docker_network"
    assert matrix.agent_to_prometheus == "http://prometheus:9090"
    assert matrix.alertmanager_to_agent_webhook.endswith(":8000/webhook")


def test_reachability_standalone_uses_host_docker_internal():
    report = DiscoveryReport(target="local")
    report.tools = [
        ToolEndpoint(
            kind=ToolKind.PROMETHEUS,
            reachable=True,
            url="http://127.0.0.1:9090",
            addressing_mode=AddressingMode.HOST_PORT,
        ),
    ]
    matrix = _build_reachability(report, target="local")
    assert matrix.agent_placement == "standalone_local"
    assert "host.docker.internal" in matrix.alertmanager_to_agent_webhook


def test_collect_requires_prometheus():
    report = DiscoveryReport(target="local")
    report.reachability = ReachabilityMatrix()
    with pytest.raises(ValueError, match="Prometheus"):
        collect(report, non_interactive=True)


def test_collect_fail_closed_without_loki_or_alertmanager(monkeypatch):
    report = DiscoveryReport(target="local")
    report.reachability = ReachabilityMatrix(
        agent_to_prometheus="http://127.0.0.1:9090",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    with pytest.raises(ValueError, match="fail closed"):
        collect(
            report,
            non_interactive=True,
            preset="generic-prometheus",
            overrides={"chat_provider": "ollama"},
        )


@pytest.fixture
def no_llm_env(monkeypatch):
    for name in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "DIAGNOSTIC_AGENT_AWS_ACCESS_KEY_ID",
        "AGENT_GRAFANA_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def no_probe(monkeypatch):
    """Endpoint reachability checks must not touch the network in unit tests."""
    monkeypatch.setattr("app.install.collect._probe_ok", lambda *_a, **_k: True)


def test_collect_prompts_for_missing_required_urls(monkeypatch, no_llm_env, no_probe):
    """Interactive mode must ask for required params discovery did not fill."""
    report = DiscoveryReport(target="local")
    report.reachability = ReachabilityMatrix()
    answers = iter(
        [
            "generic-prometheus",  # preset
            "http://prom:9090",
            "http://loki:3100",
            "http://am:9093",
            "http://agent:8000/webhook",
            "",  # skip Grafana
            "ollama",  # LLM provider
            "",  # chat model default
            "",  # embed model default
            "http://ollama:11434",  # ollama base (non-loopback: no rewrite prompt)
            "n",  # no email
            "y",  # accept review
        ]
    )
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(answers))
    monkeypatch.setattr("getpass.getpass", lambda *_a, **_k: "")

    params = collect(report, non_interactive=False, preset="generic-prometheus")
    assert params.prometheus_url == "http://prom:9090"
    assert params.loki_url == "http://loki:3100"
    assert params.alertmanager_url == "http://am:9093"
    assert params.webhook_url == "http://agent:8000/webhook"
    assert params.chat_provider == "ollama"
    assert params.webhook_disabled is False
    assert params.metrics_only is False
    assert any("interactive confirm: every parameter" in d for d in report.decisions)
    assert any("Prometheus URL confirmed" in d for d in report.decisions)
    assert any("Loki URL confirmed" in d for d in report.decisions)
    assert any("Alertmanager URL confirmed" in d for d in report.decisions)


def _discovered_report() -> DiscoveryReport:
    report = DiscoveryReport(target="local")
    report.reachability = ReachabilityMatrix(
        agent_to_prometheus="http://prometheus:9090",
        agent_to_loki="http://loki:3100",
        agent_to_alertmanager="http://alertmanager:9093",
        agent_to_grafana="http://grafana:3000",
        alertmanager_to_agent_webhook="http://diagnostic-agent:8000/webhook",
    )
    report.tools = [
        ToolEndpoint(
            kind=ToolKind.OLLAMA, reachable=True, url="http://ollama:11434"
        ),
    ]
    return report


def test_collect_confirms_discovered_defaults(monkeypatch, no_llm_env, no_probe):
    """Interactive install confirms every param even when discovery filled them."""
    report = _discovered_report()
    # Enter on every prompt -> keep discovered defaults.
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "")
    monkeypatch.setattr("getpass.getpass", lambda *_a, **_k: "")

    params = collect(report, non_interactive=False, preset="auto")
    assert params.prometheus_url == "http://prometheus:9090"
    assert params.loki_url == "http://loki:3100"
    assert params.alertmanager_url == "http://alertmanager:9093"
    assert params.grafana_url == "http://grafana:3000"
    assert params.webhook_url == "http://diagnostic-agent:8000/webhook"
    assert params.chat_provider == "ollama"
    assert any("interactive confirm: every parameter" in d for d in report.decisions)
    assert any("LLM confirmed" in d for d in report.decisions)


def test_accept_defaults_skips_prompts(no_llm_env, no_probe):
    """--accept-defaults resolves everything without reading stdin."""
    report = _discovered_report()

    def _explode(*_a, **_k):  # pragma: no cover - must never run
        raise AssertionError("accept_defaults must not prompt")

    import builtins

    original = builtins.input
    builtins.input = _explode
    try:
        params = collect(report, non_interactive=False, accept_defaults=True)
    finally:
        builtins.input = original

    assert params.prometheus_url == "http://prometheus:9090"
    assert params.loki_url == "http://loki:3100"
    assert params.chat_provider == "ollama"


def test_loopback_endpoint_rewritten_for_container(monkeypatch, no_llm_env, no_probe):
    """Loopback URLs are offered a host.docker.internal rewrite (default yes)."""
    report = DiscoveryReport(target="local")
    report.reachability = ReachabilityMatrix(
        agent_to_prometheus="http://127.0.0.1:9090",
        agent_to_loki="http://127.0.0.1:3100",
        agent_to_alertmanager="http://127.0.0.1:9093",
        alertmanager_to_agent_webhook="http://host.docker.internal:8001/webhook",
    )
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "")
    monkeypatch.setattr("getpass.getpass", lambda *_a, **_k: "")

    params = collect(
        report,
        non_interactive=False,
        preset="generic-prometheus",
        overrides={"chat_provider": "ollama"},
    )
    assert params.prometheus_url == "http://host.docker.internal:9090"
    assert params.loki_url == "http://host.docker.internal:3100"
    assert any("rewritten for container" in d for d in report.decisions)


def test_bedrock_requires_credential_decision(monkeypatch, no_llm_env, no_probe):
    """Choosing bedrock interactively must resolve credentials explicitly."""
    report = _discovered_report()
    report.tools = []  # no Ollama -> provider prompt starts from ollama default
    answers = iter(
        [
            "generic-prometheus",
            "",  # prometheus
            "",  # loki
            "",  # alertmanager
            "",  # webhook
            "",  # grafana
            "bedrock",  # provider
            "us-east-1",  # region
            "",  # chat model
            "",  # embed model
            "n",  # do NOT use ambient credentials -> prompt for keys
            "n",  # no email
            "y",  # accept review
        ]
    )
    secrets = iter(["AKIAEXAMPLE", "secret-value", ""])
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(answers))
    monkeypatch.setattr("getpass.getpass", lambda *_a, **_k: next(secrets))

    params = collect(report, non_interactive=False, preset="generic-prometheus")
    assert params.chat_provider == "bedrock_converse"
    assert params.aws_access_key_id == "AKIAEXAMPLE"
    assert params.aws_secret_access_key == "secret-value"
    assert any("explicit AWS keys" in d for d in report.decisions)


def test_bedrock_ambient_credentials_warn(monkeypatch, no_llm_env, no_probe):
    """Ambient AWS credentials are allowed but must surface a warning."""
    report = _discovered_report()
    report.tools = []
    answers = iter(
        [
            "generic-prometheus",
            "",
            "",
            "",
            "",
            "",
            "bedrock",
            "eu-west-1",
            "",
            "",
            "y",  # use ambient credentials
            "n",  # no email
            "y",  # accept review
        ]
    )
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(answers))
    monkeypatch.setattr("getpass.getpass", lambda *_a, **_k: "")

    params = collect(report, non_interactive=False, preset="generic-prometheus")
    assert params.chat_provider == "bedrock_converse"
    assert params.aws_access_key_id == ""
    assert any("No AWS keys in agent/.env" in w for w in report.warnings)
    assert '"region_name": "eu-west-1"' in params.chat_model_kwargs


def test_review_decline_restarts_collection(monkeypatch, no_llm_env, no_probe):
    """Declining the review summary re-runs the prompts."""
    report = _discovered_report()
    answers = iter(
        [
            # round 1
            "generic-prometheus", "", "", "", "", "", "ollama", "", "", "", "n",
            "n",  # decline review
            # round 2
            "spring-micrometer", "", "", "", "", "", "ollama", "", "", "", "n",
            "y",  # accept review
        ]
    )
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(answers))
    monkeypatch.setattr("getpass.getpass", lambda *_a, **_k: "")

    params = collect(report, non_interactive=False, preset="auto")
    assert params.preset == "spring-micrometer"
    assert any("re-entered parameters at review" in d for d in report.decisions)


def test_collect_degrades_with_allow_degraded(monkeypatch):
    report = DiscoveryReport(target="local")
    report.reachability = ReachabilityMatrix(
        agent_to_prometheus="http://127.0.0.1:9090",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    params = collect(
        report,
        non_interactive=True,
        allow_degraded=True,
        preset="generic-prometheus",
    )
    assert params.prometheus_url == "http://127.0.0.1:9090"
    assert params.metrics_only is True
    assert params.annotations_disabled is True
    assert params.webhook_disabled is True
    assert params.preset == "generic-prometheus"


def test_collect_fail_closed_without_llm(monkeypatch):
    report = DiscoveryReport(target="local")
    report.reachability = ReachabilityMatrix(
        agent_to_prometheus="http://127.0.0.1:9090",
        agent_to_loki="http://127.0.0.1:3100",
        agent_to_alertmanager="http://127.0.0.1:9093",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("DIAGNOSTIC_AGENT_AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="No LLM credentials"):
        collect(report, non_interactive=True, preset="generic-prometheus")


def _complete_report() -> DiscoveryReport:
    report = DiscoveryReport(target="local")
    report.reachability = ReachabilityMatrix(
        agent_placement="standalone_local",
        agent_to_prometheus="http://127.0.0.1:9090",
        agent_to_loki="http://127.0.0.1:3100",
        agent_to_alertmanager="http://127.0.0.1:9093",
        alertmanager_to_agent_webhook="http://host.docker.internal:8001/webhook",
    )
    report.tools = [
        ToolEndpoint(
            kind=ToolKind.PROMETHEUS,
            reachable=True,
            url="http://127.0.0.1:9090",
        ),
        ToolEndpoint(
            kind=ToolKind.LOKI,
            reachable=True,
            url="http://127.0.0.1:3100",
        ),
        ToolEndpoint(
            kind=ToolKind.ALERTMANAGER,
            reachable=True,
            url="http://127.0.0.1:9093",
        ),
    ]
    return report


def test_generate_and_verify(tmp_path: Path):
    report = _complete_report()
    params = collect(
        report,
        non_interactive=True,
        preset="generic-prometheus",
        overrides={"chat_provider": "ollama"},
    )
    package_root = Path(__file__).resolve().parent.parent
    written = generate(
        output=tmp_path / "out",
        report=report,
        params=params,
        package_root=package_root,
    )
    assert written
    out = tmp_path / "out"
    assert (out / "APPLY.md").is_file()
    assert (out / "install-report.json").is_file()
    assert (out / "agent" / ".env").is_file()
    assert (out / "agent" / "workspace" / "agent.yaml").is_file()
    assert (out / "agent" / "workspace" / "redaction.yaml").is_file()
    assert (out / "observability" / "alertmanager" / "route.generated.yml").is_file()
    rules = yaml.safe_load(
        (out / "observability" / "prometheus" / "alert-rules.generated.yml").read_text(
            encoding="utf-8"
        )
    )
    assert rules["groups"][0]["rules"]
    env = (out / "agent" / ".env").read_text(encoding="utf-8")
    assert "AGENT_PROMETHEUS_URL=http://127.0.0.1:9090" in env
    assert "AGENT_LOKI_URL=http://127.0.0.1:3100" in env
    assert "AGENT_REQUIRE_REDACTION=true" in env

    report_json = json.loads((out / "install-report.json").read_text(encoding="utf-8"))
    assert "params" in report_json
    # Secrets must be redacted in the report even if empty.
    assert report_json["params"]["grafana_token"] in ("", "***")

    errors = verify(out)
    assert errors == [], errors


def test_verify_rejects_incomplete_bundle_without_allow_degraded(tmp_path: Path):
    report = DiscoveryReport(target="local")
    report.reachability = ReachabilityMatrix(
        agent_to_prometheus="http://127.0.0.1:9090",
    )
    params = collect(
        report,
        non_interactive=True,
        allow_degraded=True,
        overrides={"chat_provider": "ollama", "prometheus_url": "http://127.0.0.1:9090"},
    )
    package_root = Path(__file__).resolve().parent.parent
    out = tmp_path / "out"
    generate(output=out, report=report, params=params, package_root=package_root)
    errors = verify(out, allow_degraded=False)
    assert any("AGENT_LOKI_URL" in e for e in errors)
    assert any("route.generated.yml" in e for e in errors)
    assert verify(out, allow_degraded=True) == []


def test_compose_declares_host_gateway_for_loopback_rewrite(tmp_path: Path):
    """host.docker.internal needs an explicit host-gateway mapping on Linux."""
    report = _complete_report()
    report.reachability.agent_to_prometheus = "http://host.docker.internal:9090"
    params = collect(
        report,
        non_interactive=True,
        preset="generic-prometheus",
        overrides={
            "chat_provider": "ollama",
            "prometheus_url": "http://host.docker.internal:9090",
        },
    )
    package_root = Path(__file__).resolve().parent.parent
    out = tmp_path / "out"
    generate(output=out, report=report, params=params, package_root=package_root)
    compose = (out / "agent" / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'host.docker.internal:host-gateway' in compose


def test_compose_omits_host_gateway_without_loopback(tmp_path: Path):
    report = _complete_report()
    params = collect(
        report,
        non_interactive=True,
        preset="generic-prometheus",
        overrides={"chat_provider": "ollama"},
    )
    package_root = Path(__file__).resolve().parent.parent
    out = tmp_path / "out"
    generate(output=out, report=report, params=params, package_root=package_root)
    compose = (out / "agent" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "host-gateway" not in compose


def test_generate_dry_run_writes_nothing(tmp_path: Path):
    report = DiscoveryReport(target="local")
    report.reachability = ReachabilityMatrix(
        agent_to_prometheus="http://127.0.0.1:9090",
    )
    params = collect(
        report,
        non_interactive=True,
        allow_degraded=True,
        overrides={"chat_provider": "ollama", "prometheus_url": "http://127.0.0.1:9090"},
    )
    package_root = Path(__file__).resolve().parent.parent
    generate(
        output=tmp_path / "out",
        report=report,
        params=params,
        dry_run=True,
        package_root=package_root,
    )
    assert not (tmp_path / "out").exists()


def test_generate_idempotent_backup(tmp_path: Path):
    report = DiscoveryReport(target="local")
    report.reachability = ReachabilityMatrix(
        agent_to_prometheus="http://127.0.0.1:9090",
    )
    params = collect(
        report,
        non_interactive=True,
        allow_degraded=True,
        overrides={"chat_provider": "ollama", "prometheus_url": "http://127.0.0.1:9090"},
    )
    package_root = Path(__file__).resolve().parent.parent
    out = tmp_path / "out"
    generate(output=out, report=report, params=params, package_root=package_root)
    # Mutate a generated file, then regenerate with force — backup should appear.
    target = out / "APPLY.md"
    target.write_text("stale\n", encoding="utf-8")
    generate(
        output=out, report=report, params=params, force=True, package_root=package_root
    )
    backups = list(out.glob("APPLY.md.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "stale\n"


@patch("app.install.discover.subprocess.run")
def test_discover_handles_docker_unavailable(mock_run: MagicMock):
    mock_run.side_effect = FileNotFoundError("docker")
    with patch("app.install.discover._http_probe", return_value=(False, "", "no")):
        # Without prometheus override path, discover still returns a report.
        from app.install.discover import discover

        report = discover(target="local", timeout=0.1)
        assert report.errors  # prometheus missing
        assert any("docker introspection unavailable" in w for w in report.warnings)


def test_collect_seeds_mailpit_smtp_from_container(no_llm_env, no_probe):
    """Docker-found Mailpit (even without HTTP reachability) seeds SMTP."""
    report = _discovered_report()
    report.tools.append(
        ToolEndpoint(
            kind=ToolKind.MAILPIT,
            reachable=False,
            container_name="publishi-mailpit",
            published_port=8025,
        )
    )
    params = collect(
        report,
        non_interactive=True,
        allow_degraded=True,
        overrides={"chat_provider": "ollama"},
    )
    assert params.email_enabled is True
    assert params.smtp_host == "publishi-mailpit"
    assert params.smtp_port == 1025
    assert params.smtp_starttls is False
    assert params.smtp_username == ""
    assert any("SMTP seed -> Mailpit" in d for d in report.decisions)


def test_collect_interactive_smtp_defaults_to_mailpit_client(
    monkeypatch, no_llm_env, no_probe
):
    """Without Mailpit, enabling email still defaults to Mailpit client settings."""
    report = _discovered_report()
    answers = iter(
        [
            "generic-prometheus",
            "",
            "",
            "",
            "",
            "",
            "ollama",
            "",
            "",
            "",
            "y",  # enable email
            "",  # smtp host -> host.docker.internal
            "",  # smtp port -> 1025
            "",  # from
            "",  # to
            "",  # username
            "n",  # starttls
            "y",  # accept review
        ]
    )
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(answers))
    monkeypatch.setattr("getpass.getpass", lambda *_a, **_k: "")

    params = collect(report, non_interactive=False, preset="generic-prometheus")
    assert params.email_enabled is True
    assert params.smtp_host == "host.docker.internal"
    assert params.smtp_port == 1025
    assert params.smtp_starttls is False


def test_mailpit_default_ports_and_health():
    from app.install.models import DEFAULT_PORTS, HEALTH_PATHS, MAILPIT_SMTP_PORT

    assert DEFAULT_PORTS[ToolKind.MAILPIT] == 8025
    assert MAILPIT_SMTP_PORT == 1025
    assert "/api/v1/info" in HEALTH_PATHS[ToolKind.MAILPIT]
