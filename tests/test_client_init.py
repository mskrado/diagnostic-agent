"""Tests for client-layout install generation."""
from __future__ import annotations

from pathlib import Path

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
