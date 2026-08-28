"""Tests for client-layout install generation."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from app.install.cli import _apply_param_overrides
from app.install.client_scaffold import scaffold_client_extras
from app.install.generate import generate
from app.install.models import DiscoveryReport, InstallParams


def test_generate_client_layout_writes_workspace_at_root(tmp_path: Path):
    params = InstallParams(
        preset="generic-prometheus",
        prometheus_url="http://127.0.0.1:9090",
        loki_url="http://127.0.0.1:3100",
        grafana_url="http://127.0.0.1:3000",
        alertmanager_url="http://127.0.0.1:9093",
        webhook_url="http://127.0.0.1:8001/alert",
        build_from_source=True,
        smtp_host="mailpit",
    )
    report = DiscoveryReport(target="local")
    out = tmp_path / "client"
    written = generate(
        output=out,
        report=report,
        params=params,
        package_root=Path(__file__).resolve().parent.parent,
        layout="client",
    )
    rels = {p.relative_to(out).as_posix() for p in written}
    assert "workspace/agent.yaml" in rels
    assert "agent/docker-compose.yml" in rels
    assert "agent/.env.example" in rels
    compose = (out / "agent" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "../workspace:/workspace:ro" in compose
    assert "build:" in compose
    assert "client/agent/Dockerfile" in compose


def _client_params(**kwargs) -> InstallParams:
    base = dict(
        preset="generic-prometheus",
        prometheus_url="http://127.0.0.1:9090",
        loki_url="http://127.0.0.1:3100",
        alertmanager_url="http://127.0.0.1:9093",
        webhook_url="http://127.0.0.1:8001/alert",
        build_from_source=True,
    )
    base.update(kwargs)
    return InstallParams(**base)


def test_compose_build_args_use_valid_interpolation(tmp_path: Path):
    """Build args must be `${VAR:-default}`, not a quoted f-string artefact.

    `${'BASE_IMAGE:-python:3.12-slim'}` parses as YAML but makes Compose resolve
    a variable literally named `'BASE_IMAGE`, so every client build broke.
    """
    out = tmp_path / "client"
    generate(
        output=out,
        report=DiscoveryReport(target="local"),
        params=_client_params(base_image="python:3.12-slim"),
        package_root=Path(__file__).resolve().parent.parent,
        layout="client",
    )
    compose = (out / "agent" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "${'" not in compose
    args = yaml.safe_load(compose)["services"]["diagnostic-agent"]["build"]["args"]
    assert args["BASE_IMAGE"] == "${BASE_IMAGE:-python:3.12-slim}"
    assert args["PIP_INDEX_URL"] == "${PIP_INDEX_URL:-}"
    assert args["PIP_EXTRA_INDEX_URL"] == "${PIP_EXTRA_INDEX_URL:-}"


def test_generated_dockerfile_declares_pip_args_inside_build_stage(tmp_path: Path):
    """ARGs above the first FROM are global scope and invisible to the stage."""
    out = tmp_path / "client"
    generate(
        output=out,
        report=DiscoveryReport(target="local"),
        params=_client_params(),
        package_root=Path(__file__).resolve().parent.parent,
        layout="client",
    )
    dockerfile = (out / "agent" / "Dockerfile").read_text(encoding="utf-8")
    lines = dockerfile.splitlines()
    from_at = next(i for i, line in enumerate(lines) if line.startswith("FROM "))
    for arg in ("PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL"):
        declared = next(i for i, line in enumerate(lines) if line == f"ARG {arg}=")
        assert declared > from_at, f"ARG {arg} must be re-declared after FROM"
        assert f"${arg}" in dockerfile, f"ARG {arg} is declared but never used"


def test_param_overrides_do_not_clobber_resolved_preset():
    """`--preset auto` is a sentinel that collect() resolves; never write it back."""
    params = InstallParams()
    params.preset = "spring-micrometer"
    _apply_param_overrides(params, {"preset": "auto", "base_image": "python:3.12-slim"})
    assert params.preset == "spring-micrometer"
    assert params.base_image == "python:3.12-slim"


def test_scaffold_scripts_use_the_configured_host_port(tmp_path: Path):
    client_dir = tmp_path / "client"
    client_dir.mkdir()
    scaffold_client_extras(
        client_dir=client_dir,
        report=DiscoveryReport(target="local"),
        params=_client_params(agent_host_port=9555),
        upstream_version="v1.2.3",
        repo_root=tmp_path,
    )
    for rel in ("scripts/start.sh", "scripts/status.sh", "scripts/start.ps1"):
        text = (client_dir / rel).read_text(encoding="utf-8")
        assert "__HOST_PORT__" not in text
        assert "9555" in text, f"{rel} did not pick up the configured host port"
    unit = (client_dir / "systemd" / "diagnostic-agent.service").read_text(encoding="utf-8")
    assert "--port 9555" in unit
    # A unit generated on Windows must never carry drive-letter paths.
    assert not re.search(r"^[A-Za-z]:\\\\|\\\\", unit, re.MULTILINE)
