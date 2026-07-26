"""Generate agent + observability bundles into ``--output``."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .models import DiscoveryReport, InstallParams

# Canonical alerts the shipped runbook corpus can diagnose.
# Keys: alertname -> (severity, runbook filename, generic PromQL, spring PromQL)
_ALERT_CATALOG: dict[str, dict[str, str]] = {
    "HighErrorRate": {
        "severity": "critical",
        "runbook": "runbook-high-error-rate.md",
        "service": "app",
        "generic": (
            'sum(rate(http_requests_total{code=~"5.."}[5m])) '
            '/ clamp_min(sum(rate(http_requests_total[5m])), 0.001) > 0.05'
        ),
        "spring": (
            'sum(rate(http_server_requests_seconds_count{status=~"5.."}[5m])) '
            '/ clamp_min(sum(rate(http_server_requests_seconds_count[5m])), 0.001) > 0.05'
        ),
        "summary": "Elevated 5xx error rate",
    },
    "PostgresConnectivity": {
        "severity": "critical",
        "runbook": "runbook-postgres-connectivity.md",
        "service": "postgres",
        "generic": 'up{job=~".*postgres.*"} == 0',
        "spring": 'up{job=~".*postgres.*"} == 0',
        "summary": "Postgres target down",
    },
    "HostDiskPressure": {
        "severity": "warning",
        "runbook": "runbook-host-disk-pressure.md",
        "service": "node",
        "generic": (
            '(node_filesystem_avail_bytes{fstype!~"tmpfs|overlay"} '
            '/ node_filesystem_size_bytes) < 0.1'
        ),
        "spring": (
            '(node_filesystem_avail_bytes{fstype!~"tmpfs|overlay"} '
            '/ node_filesystem_size_bytes) < 0.1'
        ),
        "summary": "Host filesystem below 10% free",
    },
    "ContainerRestartLoop": {
        "severity": "warning",
        "runbook": "runbook-container-restart-loop.md",
        "service": "app",
        "generic": "increase(container_restart_count[15m]) > 3",
        "spring": "increase(container_restart_count[15m]) > 3",
        "summary": "Container restarting repeatedly",
    },
}


def generate(
    *,
    output: Path,
    report: DiscoveryReport,
    params: InstallParams,
    dry_run: bool = False,
    force: bool = False,
    package_root: Path | None = None,
) -> list[Path]:
    """Write the install bundle. Returns list of paths that would be / were written."""
    output = output.resolve()
    package_root = package_root or Path(__file__).resolve().parent.parent.parent
    written: list[Path] = []

    plan: list[tuple[Path, str]] = []

    # --- agent/ ---
    agent_dir = output / "agent"
    workspace = agent_dir / "workspace"
    plan.append((agent_dir / "Dockerfile", _dockerfile()))
    plan.append((agent_dir / "docker-compose.yml", _compose(params)))
    plan.append((agent_dir / ".env", _env_file(params)))
    plan.append((agent_dir / ".gitignore", ".env\ninstall-report.json\n"))
    plan.append((workspace / "agent.yaml", _agent_yaml(params)))
    plan.append((workspace / "metrics_profile.yaml", f"extends: {params.preset}\n"))
    plan.append((workspace / "logs_profile.yaml", f"extends: {params.preset}\n"))
    plan.append((workspace / "prompt_profile.yaml", f"extends: {params.preset}\n"))
    plan.append((workspace / "redaction.yaml", f"extends: {params.preset}\n"))
    plan.append((workspace / "service_map.yaml", _service_map(params.preset)))
    plan.append((workspace / "scenarios.yaml", _scenarios_yaml(params.preset)))

    # Seed runbooks from the package corpus (intersecting catalog).
    runbooks_src = package_root / "runbooks"
    for alert in _ALERT_CATALOG.values():
        name = alert["runbook"]
        src = runbooks_src / name
        if src.is_file():
            plan.append((workspace / "runbooks" / name, src.read_text(encoding="utf-8")))

    # --- observability/ ---
    obs = output / "observability"
    plan.append(
        (
            obs / "prometheus" / "alert-rules.generated.yml",
            _alert_rules(params.preset),
        )
    )
    if not params.webhook_disabled:
        plan.append(
            (
                obs / "alertmanager" / "route.generated.yml",
                _alertmanager_route(params),
            )
        )
    plan.append(
        (obs / "promtail" / "promtail.generated.yaml", _promtail_snippet(params))
    )
    plan.append((obs / "grafana" / "README.md", _grafana_readme(params)))

    # --- report + APPLY ---
    report_body = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "discovery": report.to_dict(),
        "params": params.to_public_dict(),
    }
    plan.append(
        (
            output / "install-report.json",
            json.dumps(report_body, indent=2, sort_keys=True) + "\n",
        )
    )
    plan.append((output / "APPLY.md", _apply_md(params, report)))

    if dry_run:
        for path, content in plan:
            written.append(path)
            rel = path.relative_to(output) if path.is_relative_to(output) else path
            print(f"DRY-RUN would write {rel} ({len(content)} bytes)")
        return written

    output.mkdir(parents=True, exist_ok=True)
    for path, content in plan:
        _write_file(path, content, force=force)
        written.append(path)
    return written


def _write_file(path: Path, content: str, *, force: bool) -> None:
    """Write ``content``, keeping a timestamped backup when replacing a file.

    ``force`` is reserved for callers that previously refused overwrites; every
    differing write still gets a ``*.bak.<utc>`` sibling.
    """
    _ = force  # overwrites are always allowed; backups make them safe
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing == content:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = path.with_name(f"{path.name}.bak.{stamp}")
        shutil.copy2(path, backup)
    path.write_text(content, encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------
def _dockerfile() -> str:
    return """\
# Build the diagnostic-agent image from the published Dockerfile context,
# OR pull the prebuilt image instead (preferred):
#   docker pull ghcr.io/mskrado/diagnostic-agent:latest
#
# This Dockerfile is a thin convenience wrapper that uses the published image
# as the base so hosts don't need the agent source tree.
ARG AGENT_IMAGE=ghcr.io/mskrado/diagnostic-agent:latest
FROM ${AGENT_IMAGE}
# Workspace is bind-mounted at runtime to /workspace (see docker-compose.yml).
"""


def _compose(params: InstallParams) -> str:
    network_block = ""
    service_network = ""
    if params.docker_network:
        network_block = f"""
networks:
  default:
    name: {params.docker_network}
    external: true
"""
        service_network = "    networks:\n      - default\n"

    return f"""\
# Generated by `diag install`. Start with:
#   docker compose --env-file .env up -d
services:
  diagnostic-agent:
    image: ${{DIAGNOSTIC_AGENT_IMAGE:-{params.agent_image}}}
    container_name: {params.agent_container_name}
    ports:
      - "{params.agent_host_port}:8000"
    env_file: .env
    environment:
      AGENT_WORKSPACE: /workspace
    volumes:
      - ./workspace:/workspace:ro
      - agent_audit:/app/audit
      - agent_chroma:/app/chroma_db
    restart: unless-stopped
{service_network}volumes:
  agent_audit:
  agent_chroma:
{network_block}"""


def _env_file(params: InstallParams) -> str:
    lines = [
        "# Generated by diag install -- do not commit secrets.",
        f"AGENT_DEFAULT_PRESET={params.preset}",
        f"AGENT_PROMETHEUS_URL={params.prometheus_url}",
        f"AGENT_LOKI_URL={params.loki_url or 'http://loki:3100'}",
        f"AGENT_GRAFANA_URL={params.grafana_url or 'http://grafana:3000'}",
        f"AGENT_GRAFANA_TOKEN={params.grafana_token}",
        f"AGENT_GRAFANA_ANNOTATIONS_ENABLED="
        f"{str(params.grafana_annotations_enabled).lower()}",
        f"AGENT_CHAT_PROVIDER={params.chat_provider}",
        f"AGENT_CHAT_MODEL={params.chat_model}",
        f"AGENT_EMBED_PROVIDER={params.embed_provider}",
        f"AGENT_EMBED_MODEL={params.embed_model}",
        f"AGENT_CHAT_MODEL_KWARGS={params.chat_model_kwargs}",
        f"AGENT_EMBED_MODEL_KWARGS={params.embed_model_kwargs}",
        "AGENT_REQUIRE_REDACTION=true",
        "AGENT_RAG_ENABLED=true",
        f"AGENT_EMAIL_ENABLED={str(params.email_enabled).lower()}",
        f"AGENT_EMAIL_TO={params.email_to}",
        f"AGENT_SMTP_HOST={params.smtp_host}",
        f"AGENT_SMTP_PORT={params.smtp_port}",
        f"AGENT_SMTP_FROM={params.smtp_from}",
        f"AGENT_SMTP_USERNAME={params.smtp_username}",
        f"AGENT_SMTP_PASSWORD={params.smtp_password}",
        f"AGENT_SMTP_STARTTLS={str(params.smtp_starttls).lower()}",
        f"DIAGNOSTIC_AGENT_IMAGE={params.agent_image}",
    ]
    if params.openai_api_key:
        lines.append(f"OPENAI_API_KEY={params.openai_api_key}")
    if params.anthropic_api_key:
        lines.append(f"ANTHROPIC_API_KEY={params.anthropic_api_key}")
    if params.google_api_key:
        lines.append(f"GOOGLE_API_KEY={params.google_api_key}")
    if params.aws_access_key_id:
        lines.append(f"AWS_ACCESS_KEY_ID={params.aws_access_key_id}")
        lines.append(f"AWS_SECRET_ACCESS_KEY={params.aws_secret_access_key}")
        lines.append(f"AWS_REGION={params.aws_region}")
    return "\n".join(lines) + "\n"


def _agent_yaml(params: InstallParams) -> str:
    return (
        "# Generated by diag install\n"
        "schema: 1\n"
        f"extends: {params.preset}\n"
        "profile: .\n"
        "runbooks: ./runbooks\n"
        "scenarios: ./scenarios.yaml\n"
    )


def _service_map(preset: str) -> str:
    if preset == "spring-micrometer":
        return """\
# Generated starter topology -- edit to match your stack.
services:
  api-gateway:
    kind: gateway
    upstream: []
    downstream: [platform-service]
    description: "API gateway"
  platform-service:
    kind: monolith
    upstream: [api-gateway]
    downstream: [postgres, redis]
    description: "Application monolith"
  postgres:
    kind: database
    upstream: [platform-service]
    downstream: []
    log_services: [platform-service]
  redis:
    kind: redis
    upstream: [platform-service]
    downstream: []
    log_services: [platform-service]
module_dependencies: {}
"""
    return """\
# Generated starter topology -- edit to match your stack.
services:
  api:
    kind: http
    upstream: []
    downstream: [app]
  app:
    kind: monolith
    upstream: [api]
    downstream: [postgres]
  postgres:
    kind: database
    upstream: [app]
    downstream: []
    log_services: [app]
module_dependencies: {}
"""


def _scenarios_yaml(preset: str) -> str:
    scenarios = []
    for alertname, meta in _ALERT_CATALOG.items():
        scenarios.append(
            {
                "id": alertname.lower(),
                "runbook": meta["runbook"],
                "labels": {
                    "alertname": alertname,
                    "service": meta["service"],
                    "severity": meta["severity"],
                },
                "annotations": {"summary": meta["summary"]},
            }
        )
    return yaml.safe_dump({"scenarios": scenarios}, sort_keys=False)


def _alert_rules(preset: str) -> str:
    expr_key = "spring" if preset == "spring-micrometer" else "generic"
    rules = []
    for alertname, meta in _ALERT_CATALOG.items():
        rules.append(
            {
                "alert": alertname,
                "expr": meta[expr_key],
                "for": "5m",
                "labels": {
                    "severity": meta["severity"],
                    "service": meta["service"],
                },
                "annotations": {
                    "summary": meta["summary"],
                    "runbook": meta["runbook"],
                },
            }
        )
    doc = {
        "groups": [
            {
                "name": "diagnostic-agent.generated",
                "rules": rules,
            }
        ]
    }
    header = (
        "# Generated by diag install -- only alerts the agent can diagnose.\n"
        "# Merge into Prometheus rule_files and reload.\n"
    )
    return header + yaml.safe_dump(doc, sort_keys=False)


def _alertmanager_route(params: InstallParams) -> str:
    doc = {
        "route": {
            "routes": [
                {
                    "matchers": ['severity=~"warning|critical"'],
                    "receiver": "diagnostic-agent",
                    "continue": True,
                }
            ]
        },
        "receivers": [
            {
                "name": "diagnostic-agent",
                "webhook_configs": [
                    {
                        "url": params.webhook_url,
                        "send_resolved": True,
                    }
                ],
            }
        ],
    }
    header = (
        "# Generated by diag install -- additive route/receiver.\n"
        "# Merge into your Alertmanager config (preserve existing receivers).\n"
    )
    return header + yaml.safe_dump(doc, sort_keys=False)


def _promtail_snippet(params: InstallParams) -> str:
    _ = params
    return """\
# Generated by diag install -- ensure container/log pipelines emit `service=`.
# The agent queries Loki with `{service="<name>"}` from service_map.yaml.
#
# Example scrape_config relabel (Docker SD):
scrape_configs:
  - job_name: docker
    docker_sd_configs:
      - host: unix:///var/run/docker.sock
        refresh_interval: 5s
    relabel_configs:
      - source_labels: ['__meta_docker_container_name']
        regex: '/(.*)'
        target_label: service
      - source_labels: ['__meta_docker_container_log_stream']
        target_label: stream
"""


def _grafana_readme(params: InstallParams) -> str:
    return f"""\
# Grafana wiring

Annotations require a service-account token with org-level annotation write
access (Editor on Grafana OSS).

Prometheus / Loki datasources should already exist if Grafana is in use.

## Provision a token

1. Grafana -> Administration -> Service accounts -> Add service account
   - Name: `diagnostic-agent`
   - Role: Editor (OSS minimum for org annotations)
2. Add token -> copy once into `agent/.env` as `AGENT_GRAFANA_TOKEN=...`
3. Set `AGENT_GRAFANA_ANNOTATIONS_ENABLED=true` and restart the agent.

Detected Grafana URL: `{params.grafana_url or "(not detected)"}`
"""


def _apply_md(params: InstallParams, report: DiscoveryReport) -> str:
    lines = [
        "# Apply instructions",
        "",
        "Generated by `diag install`. Review files, then apply in order.",
        "",
        "## 1. Agent",
        "",
        "```bash",
        "cd agent",
        "docker compose --env-file .env up -d",
        "# health: curl -sf http://127.0.0.1:"
        f"{params.agent_host_port}/health",
        "```",
        "",
        "Validate the workspace without an LLM:",
        "",
        "```bash",
        f"docker run --rm -v \"$PWD/agent/workspace:/workspace:ro\" "
        f"{params.agent_image} sh -c 'diag validate && diag lint'",
        "```",
        "",
        "## 2. Prometheus alert rules",
        "",
        "Copy `observability/prometheus/alert-rules.generated.yml` into your",
        "Prometheus `rule_files` directory (or merge the `diagnostic-agent.generated`",
        "group), then reload:",
        "",
        "```bash",
        "curl -X POST http://<prometheus>/-/reload",
        "```",
        "",
    ]
    if params.webhook_disabled:
        lines += [
            "## 3. Alertmanager",
            "",
            "Alertmanager was not detected. The agent can still be invoked via",
            "`POST /alert` manually. When you add Alertmanager, merge",
            "`observability/alertmanager/route.generated.yml` and reload.",
            "",
        ]
    else:
        lines += [
            "## 3. Alertmanager webhook",
            "",
            "Merge the `diagnostic-agent` receiver and route from",
            "`observability/alertmanager/route.generated.yml` into your live",
            f"config. Webhook URL: `{params.webhook_url}`",
            "",
            "```bash",
            "curl -X POST http://<alertmanager>/-/reload",
            "```",
            "",
        ]
    lines += [
        "## 4. Promtail / Loki labels",
        "",
        "Ensure log streams carry a `service=` label matching `service_map.yaml`.",
        "See `observability/promtail/promtail.generated.yaml`.",
        "",
        "## 5. Grafana annotations",
        "",
        "Follow `observability/grafana/README.md` to mint `AGENT_GRAFANA_TOKEN`.",
        "",
        "## Discovery summary",
        "",
    ]
    for tool in report.tools:
        status = "ok" if tool.reachable else "missing"
        lines.append(
            f"- **{tool.kind.value}**: {status}"
            + (f" -- `{tool.url}`" if tool.url else "")
        )
    if report.warnings:
        lines += ["", "### Warnings", ""]
        lines.extend(f"- {w}" for w in report.warnings)
    if report.errors:
        lines += ["", "### Errors", ""]
        lines.extend(f"- {e}" for e in report.errors)
    lines.append("")
    return "\n".join(lines)
