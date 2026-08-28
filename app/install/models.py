"""Data models for discovery inventory, reachability, and install plans."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class AddressingMode(str, Enum):
    """How a probe successfully reached a tool."""

    DOCKER_DNS = "docker_dns"
    HOST_PORT = "host_port"
    REMOTE_HTTP = "remote_http"
    UNKNOWN = "unknown"


class ToolKind(str, Enum):
    PROMETHEUS = "prometheus"
    LOKI = "loki"
    ALERTMANAGER = "alertmanager"
    GRAFANA = "grafana"
    TEMPO = "tempo"
    PROMTAIL = "promtail"
    NODE_EXPORTER = "node_exporter"
    CADVISOR = "cadvisor"
    OLLAMA = "ollama"
    MAILPIT = "mailpit"


# Well-known host ports used when Docker topology is unavailable.
DEFAULT_PORTS: dict[ToolKind, int] = {
    ToolKind.PROMETHEUS: 9090,
    ToolKind.LOKI: 3100,
    ToolKind.ALERTMANAGER: 9093,
    ToolKind.GRAFANA: 3000,
    ToolKind.TEMPO: 3200,
    ToolKind.PROMTAIL: 9080,
    ToolKind.NODE_EXPORTER: 9100,
    ToolKind.CADVISOR: 8080,
    ToolKind.OLLAMA: 11434,
    # Mailpit HTTP UI / API (SMTP relay stays on MAILPIT_SMTP_PORT).
    ToolKind.MAILPIT: 8025,
}

# Mailpit SMTP listen port (separate from the HTTP UI in DEFAULT_PORTS).
MAILPIT_SMTP_PORT = 1025

# Image name substrings -> tool kind (matched case-insensitively against image).
IMAGE_HINTS: list[tuple[str, ToolKind]] = [
    ("prom/prometheus", ToolKind.PROMETHEUS),
    ("prometheus", ToolKind.PROMETHEUS),
    ("grafana/loki", ToolKind.LOKI),
    ("/loki:", ToolKind.LOKI),
    ("prom/alertmanager", ToolKind.ALERTMANAGER),
    ("alertmanager", ToolKind.ALERTMANAGER),
    ("grafana/grafana", ToolKind.GRAFANA),
    ("grafana/tempo", ToolKind.TEMPO),
    ("grafana/promtail", ToolKind.PROMTAIL),
    ("promtail", ToolKind.PROMTAIL),
    ("node-exporter", ToolKind.NODE_EXPORTER),
    ("cadvisor", ToolKind.CADVISOR),
    ("ollama/ollama", ToolKind.OLLAMA),
    ("mailpit", ToolKind.MAILPIT),
]

# Health / readiness paths used during HTTP probing.
HEALTH_PATHS: dict[ToolKind, list[str]] = {
    ToolKind.PROMETHEUS: ["/-/ready", "/api/v1/status/buildinfo"],
    ToolKind.LOKI: ["/ready"],
    ToolKind.ALERTMANAGER: ["/-/ready", "/api/v2/status"],
    ToolKind.GRAFANA: ["/api/health"],
    ToolKind.TEMPO: ["/ready"],
    ToolKind.PROMTAIL: ["/ready"],
    ToolKind.OLLAMA: ["/api/tags"],
    ToolKind.MAILPIT: ["/api/v1/info"],
}


@dataclass
class ToolEndpoint:
    """One discovered observability tool."""

    kind: ToolKind
    reachable: bool = False
    url: str = ""
    addressing_mode: AddressingMode = AddressingMode.UNKNOWN
    version: str = ""
    container_name: str = ""
    docker_network: str = ""
    published_port: int | None = None
    confidence: str = "low"  # high | medium | low
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["addressing_mode"] = self.addressing_mode.value
        return d


@dataclass
class ReachabilityMatrix:
    """Bidirectional addressing for agent ↔ stack wiring."""

    agent_placement: str = "standalone_local"
    # URLs the *agent* should use to pull from tools.
    agent_to_prometheus: str = ""
    agent_to_loki: str = ""
    agent_to_grafana: str = ""
    agent_to_alertmanager: str = ""
    # URL Alertmanager should use to push webhooks to the agent.
    # Path must be /alert — that is the only route app.main serves.
    alertmanager_to_agent_webhook: str = "http://diagnostic-agent:8000/alert"
    agent_container_name: str = "diagnostic-agent"
    agent_host_port: int = 8001
    shared_docker_network: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InstallParams:
    """Fully-resolved parameters required to run the agent + wire observability."""

    preset: str = "generic-prometheus"
    prometheus_url: str = ""
    loki_url: str = ""
    grafana_url: str = ""
    alertmanager_url: str = ""
    grafana_token: str = ""
    grafana_annotations_enabled: bool = True
    chat_provider: str = "ollama"
    chat_model: str = "mistral:7b-instruct"
    embed_provider: str = "ollama"
    embed_model: str = "nomic-embed-text"
    chat_model_kwargs: str = "{}"
    embed_model_kwargs: str = "{}"
    email_enabled: bool = False
    email_to: str = "ops@localhost"
    smtp_host: str = ""
    smtp_port: int = 1025
    smtp_from: str = "diagnostic-agent@localhost"
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = False
    agent_image: str = "ghcr.io/mskrado/diagnostic-agent:latest"
    agent_host_port: int = 8001
    agent_container_name: str = "diagnostic-agent"
    # Self-build / air-gapped client fork options (diag init).
    base_image: str = "python:3.12-slim"
    build_from_source: bool = False
    pip_index_url: str = ""
    pip_extra_index_url: str = ""
    local_image_tag: str = "diagnostic-agent:local"
    webhook_url: str = "http://diagnostic-agent:8000/alert"
    docker_network: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    google_api_key: str = ""
    aws_region: str = "us-east-1"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    # Degradation flags
    metrics_only: bool = False
    annotations_disabled: bool = False
    webhook_disabled: bool = False

    def to_public_dict(self) -> dict[str, Any]:
        """Serialize for install-report.json -- secrets redacted."""
        d = asdict(self)
        for key in (
            "grafana_token",
            "smtp_password",
            "openai_api_key",
            "anthropic_api_key",
            "google_api_key",
            "aws_access_key_id",
            "aws_secret_access_key",
        ):
            if d.get(key):
                d[key] = "***"
        return d


def client_image_ref(params: InstallParams) -> str:
    """Image reference for compose: local build tag or pulled image."""
    if params.build_from_source:
        return params.local_image_tag
    return params.agent_image


@dataclass
class DiscoveryReport:
    """Full discovery result persisted to install-report.json."""

    target: str
    tools: list[ToolEndpoint] = field(default_factory=list)
    reachability: ReachabilityMatrix = field(default_factory=ReachabilityMatrix)
    decisions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def tool(self, kind: ToolKind) -> ToolEndpoint | None:
        for t in self.tools:
            if t.kind == kind and t.reachable:
                return t
        for t in self.tools:
            if t.kind == kind:
                return t
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "tools": [t.to_dict() for t in self.tools],
            "reachability": self.reachability.to_dict(),
            "decisions": self.decisions,
            "warnings": self.warnings,
            "errors": self.errors,
        }
