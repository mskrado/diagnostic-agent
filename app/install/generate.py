"""Generate agent + observability bundles into ``--output``."""
from __future__ import annotations

import json
import re
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
    plan.append((agent_dir / "docker-compose.yml", _compose(params, output)))
    plan.append((agent_dir / ".env", _env_file(params)))
    plan.append((agent_dir / ".gitignore", ".env\ninstall-report.json\n"))
    plan.append((workspace / "agent.yaml", _agent_yaml(params)))

    # Profile + topology: spring hosts get the full modular-monolith example;
    # generic hosts get documented stubs + a starter service_map.
    for rel, content in _workspace_profile_files(package_root, params.preset):
        plan.append((workspace / rel, content))

    plan.append((workspace / "scenarios.yaml", _scenarios_yaml(params.preset)))
    # Full runbook corpus (RAG). Alert rules stay catalog-intersected separately.
    runbooks_src = package_root / "runbooks"
    for src in sorted(runbooks_src.glob("runbook-*.md")):
        plan.append(
            (workspace / "runbooks" / src.name, src.read_text(encoding="utf-8"))
        )
    readme = runbooks_src / "README.md"
    if readme.is_file():
        plan.append(
            (workspace / "runbooks" / "README.md", readme.read_text(encoding="utf-8"))
        )
    else:
        plan.append((workspace / "runbooks" / "README.md", _runbooks_readme()))

    # Blind eval dataset — install output is enough for `diag eval -w … blind`.
    blind_src = package_root / "eval" / "blind_eval_dataset.yaml"
    if blind_src.is_file():
        plan.append(
            (workspace / "blind_eval.yaml", blind_src.read_text(encoding="utf-8"))
        )
    else:
        report.warnings.append(
            "eval/blind_eval_dataset.yaml missing from package — "
            "workspace will not include blind_eval.yaml"
        )

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


def _compose_project_name(output: Path) -> str:
    """Stable Compose project name so sibling install bundles do not collide.

    Compose defaults the project name to the parent directory of the compose
    file. Every install puts that file under ``…/agent/``, so two bundles would
    share the project name ``agent`` and ``up -d`` would recreate each other.
    Derive the name from the install output directory instead.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", output.resolve().name.lower()).strip("-")
    if not slug or slug == "agent":
        parent = output.resolve().parent.name
        slug = re.sub(r"[^a-z0-9]+", "-", parent.lower()).strip("-") or "diagnostic"
    return f"{slug}-agent"


def _compose(params: InstallParams, output: Path) -> str:
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

    # host.docker.internal is implicit on Docker Desktop but needs an explicit
    # host-gateway mapping on Linux, so declare it whenever a URL relies on it.
    host_gateway_block = ""
    if any(
        "host.docker.internal" in value
        for value in (
            params.prometheus_url,
            params.loki_url,
            params.grafana_url,
            params.chat_model_kwargs,
            params.embed_model_kwargs,
            params.smtp_host,
        )
    ):
        host_gateway_block = (
            '    extra_hosts:\n      - "host.docker.internal:host-gateway"\n'
        )

    project_name = _compose_project_name(output)

    return f"""\
# Generated by `diag install`.
#
# HOW TO USE
#   docker compose --env-file .env up -d
#   curl -sf http://127.0.0.1:{params.agent_host_port}/health
#
# WHAT THIS FILE DOES
#   Runs the published diagnostic-agent image, injects agent/.env, and bind-mounts
#   ./workspace -> /workspace:ro (AGENT_WORKSPACE). Audit JSONL and Chroma RAG
#   data persist in named volumes.
#
# CONFIGURE
#   - Change the host port mapping if 8001 (or the discovered port) conflicts.
#   - Pin DIAGNOSTIC_AGENT_IMAGE in .env for reproducible deploys.
#   - If the agent shares an observability Docker network, `networks.default`
#     is set to that external network so container DNS names work.
#   - Edit ./workspace (not this file) for service_map / profiles / runbooks.
#   Full reference: docs/WORKSPACE.md and docs/INSTALL.md
#
# The project name is pinned (not left as the parent directory "agent") so that
# two install bundles under different output paths do not share a Compose
# project and recreate each other's containers.
name: {project_name}
services:
  diagnostic-agent:
    image: ${{DIAGNOSTIC_AGENT_IMAGE:-{params.agent_image}}}
    container_name: {params.agent_container_name}
    ports:
      - "{params.agent_host_port}:8000"
    env_file: .env
    environment:
      AGENT_WORKSPACE: /workspace
      # The published image may ship AGENT_PROFILE_DIR="" and `diag serve` only
      # setdefault()s it — an empty string would shadow the mounted workspace.
      AGENT_PROFILE_DIR: /workspace
      AGENT_RUNBOOKS_PATH: /workspace/runbooks
    volumes:
      - ./workspace:/workspace:ro
      - agent_audit:/app/audit
      - agent_chroma:/app/chroma_db
    restart: unless-stopped
{host_gateway_block}{service_network}volumes:
  agent_audit:
  agent_chroma:
{network_block}"""


def _env_file(params: InstallParams) -> str:
    lines = [
        "# Generated by diag install -- do not commit secrets.",
        "#",
        "# WHAT THIS FILE DOES",
        "#   Runtime configuration for the agent container (Compose env_file).",
        "#   URLs, LLM provider/model, SMTP, Grafana token, and safety flags.",
        "#",
        "# CONFIGURE",
        "#   - Keep AGENT_DEFAULT_PRESET identical to workspace/agent.yaml `extends`.",
        "#   - Set LLM credentials for your provider (OPENAI_API_KEY, AWS_*, ...).",
        "#   - Optional: AGENT_GRAFANA_TOKEN (glsa_...) to enable annotations.",
        "#   - SMTP: Mailpit is typically host/container :1025 with STARTTLS=false.",
        "#   - AGENT_REQUIRE_REDACTION=true refuses start when redaction rules resolve to 0.",
        "#   Full reference: docs/INSTALL.md (parameter tables) + docs/WORKSPACE.md",
        "#",
        f"AGENT_DEFAULT_PRESET={params.preset}",
        f"AGENT_PROMETHEUS_URL={params.prometheus_url}",
        f"AGENT_LOKI_URL={params.loki_url}",
        f"AGENT_GRAFANA_URL={params.grafana_url}",
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
        "AGENT_EMAIL_ATTACH_AUDIT=true",
        "AGENT_EMAIL_ATTACH_AUDIT_MAX_BYTES=262144",
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
    return f"""\
# Generated by diag install.
#
# WHAT THIS FILE DOES
#   Workspace manifest: schema, preset (`extends`), and paths to profile /
#   runbooks / scenarios / blind_eval. Self-sufficient — validate/lint/eval
#   against this directory only (no host monorepo required).
#
# CONFIGURE
#   1. Set `extends` to match your metrics (`spring-micrometer` for Spring Boot).
#   2. Keep AGENT_DEFAULT_PRESET in ../.env identical to `extends`.
#   3. Optionally set agent_version.
#   Full reference: docs/WORKSPACE.md
schema: 1
extends: {params.preset}
profile: .
runbooks: ./runbooks
scenarios: ./scenarios.yaml
blind_eval: ./blind_eval.yaml
"""



_PROFILE_STUB_DOCS: dict[str, tuple[str, str]] = {
    "metrics_profile": (
        "PromQL templates ({service} / {window}) used when probing metrics.",
        "Keep `extends` aligned with agent.yaml. Override `templates:` only when "
        "your metric names differ from the preset. Wrong preset => empty/wrong queries.",
    ),
    "logs_profile": (
        "Loki retrieval settings: service label, level gate, module regex, filters.",
        "Set service_label (usually service), level_filter, and alert_line_filters "
        "keyed by alertname. See examples/spring-modular-monolith/logs_profile.yaml.",
    ),
    "prompt_profile": (
        "LLM framing: platform_description + tool_run_hints for YOUR stack.",
        "Describe real services/deps and paste curl/docker/actuator examples. "
        "Core safety rules stay in agent code and cannot be overridden here.",
    ),
    "redaction": (
        "Secret + tenant/PII scrubbing applied before reports/email/annotations.",
        "Rules accumulate on `extends`. Add tenant/PII rules under `rules:`. "
        "Zero resolved rules => refuse to start (AGENT_REQUIRE_REDACTION).",
    ),
}

def _profile_stub(kind: str, preset: str) -> str:
    purpose, configure = _PROFILE_STUB_DOCS[kind]
    return f"""\
# Generated by diag install -- stub for {kind}.yaml.
#
# WHAT THIS FILE DOES
#   {purpose}
#
# HOW THE AGENT USES IT
#   Loaded as part of the integration profile and merged through the `extends`
#   chain onto the built-in preset shipped in the image.
#
# CONFIGURE
#   {configure}
#   Full reference: docs/WORKSPACE.md
extends: {preset}
"""


def _workspace_profile_files(
    package_root: Path, preset: str
) -> list[tuple[str, str]]:
    """Return (relative_path, content) pairs for the workspace profile."""
    if preset == "spring-micrometer":
        example = package_root / "examples" / "spring-modular-monolith"
        files: list[tuple[str, str]] = []
        for name in (
            "metrics_profile.yaml",
            "logs_profile.yaml",
            "prompt_profile.yaml",
            "redaction.yaml",
            "service_map.yaml",
        ):
            src = example / name
            if src.is_file():
                header = (
                    f"# Seeded from examples/spring-modular-monolith/{name}\n"
                    "# Edit to match your stack; install output stays self-contained.\n"
                )
                body = src.read_text(encoding="utf-8")
                if not body.lstrip().startswith("# Seeded from"):
                    body = header + body
                files.append((name, body))
            else:
                kind = name.removesuffix(".yaml")
                if kind == "service_map":
                    files.append((name, _service_map(preset)))
                else:
                    files.append((name, _profile_stub(kind, preset)))
        return files

    return [
        ("metrics_profile.yaml", _profile_stub("metrics_profile", preset)),
        ("logs_profile.yaml", _profile_stub("logs_profile", preset)),
        ("prompt_profile.yaml", _profile_stub("prompt_profile", preset)),
        ("redaction.yaml", _profile_stub("redaction", preset)),
        ("service_map.yaml", _service_map(preset)),
    ]


def _service_map(preset: str) -> str:
    header = """\
# Generated by diag install -- STARTER topology (edit to match your stack).
#
# WHAT THIS FILE DOES
#   Declares services, dependency edges, and log routing for blast radius.
#
# HOW THE AGENT USES IT
#   Expands related services from the alerting service=, selects dependency
#   PromQL probes by `kind`, and may redirect Loki via log_services / log_selector.
#   An empty or wrong map => no useful blast radius (agent still runs).
#
# CONFIGURE
#   1. Rename services to match alert labels and Loki service= values.
#   2. Set kind (http|gateway|monolith|database|redis|...).
#   3. Fill upstream/downstream edges; add descriptions for the LLM.
#   4. For DBs without their own logs, set log_services: [app-service-name].
#   Full reference: docs/WORKSPACE.md
#   Example: examples/spring-modular-monolith/service_map.yaml
"""
    if preset == "spring-micrometer":
        return (
            header
            + """\
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
        )
    return (
        header
        + """\
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
    )


def _scenarios_yaml(preset: str) -> str:
    scenarios = []
    for alertname, meta in _ALERT_CATALOG.items():
        service = meta["service"]
        # Spring modular-monolith maps app process logs to platform-service.
        if preset == "spring-micrometer" and service == "app":
            service = "platform-service"
        scenarios.append(
            {
                "id": alertname.lower(),
                "runbook": meta["runbook"],
                "labels": {
                    "alertname": alertname,
                    "service": service,
                    "severity": meta["severity"],
                },
                "annotations": {"summary": meta["summary"]},
            }
        )
    body = yaml.safe_dump({"scenarios": scenarios}, sort_keys=False)
    header = """\
# Generated by diag install.
#
# WHAT THIS FILE DOES
#   Alert label sets paired with runbooks for `diag lint` / `diag e2e`.
#
# HOW THE AGENT USES IT
#   Corpus tools verify coverage; e2e posts each scenario to a running agent.
#   This is NOT your Prometheus rule file (see observability/prometheus/).
#
# CONFIGURE
#   - Keep labels.alertname / service aligned with Alertmanager and runbooks/.
#   - Trim scenarios you will not fire; add host-specific ones as you author
#     new runbooks.
#   Full reference: docs/WORKSPACE.md
"""
    return header + body


def _runbooks_readme() -> str:
    return """\
# Runbooks (RAG corpus)

## What this directory does

Markdown playbooks the agent indexes when `AGENT_RAG_ENABLED=true`. On each
diagnosis, similar excerpts are retrieved into the LLM context.

## How the agent uses it

- Filenames are typically `runbook-<topic>.md`.
- `scenarios.yaml` and Prometheus alert annotations often reference the same names.
- Install seeds runbooks that intersect the generated alert catalog.

## Configure

1. Keep seeded runbooks for alerts you actually route to the agent.
2. Add host-specific playbooks with clear symptoms, checks, and remediations.
3. After large corpus changes, recreate the agent (or clear the `agent_chroma`
   volume) so the vector index rebuilds.
4. Validate without an LLM: `diag lint` (from an image mount of this workspace).

Full reference: `docs/WORKSPACE.md` and `runbooks/README.md` in the agent repo.
"""


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
        "# Generated by diag install.\n"
        "#\n"
        "# WHAT THIS FILE DOES\n"
        "#   Prometheus rule group for alerts that intersect the shipped runbook\n"
        "#   catalog (so the agent can diagnose them).\n"
        "#\n"
        "# HOW IT IS USED\n"
        "#   Merge into your Prometheus `rule_files` (additive). Not a full\n"
        "#   replacement for existing rules. Reload Prometheus after merge.\n"
        "#\n"
        "# CONFIGURE\n"
        "#   - Align `service` labels with workspace/service_map.yaml names.\n"
        "#   - Trim rules you will not route to the agent.\n"
        "#   See APPLY.md and docs/INSTALL.md.\n"
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
        "# Generated by diag install.\n"
        "#\n"
        "# WHAT THIS FILE DOES\n"
        "#   Additive Alertmanager route + webhook receiver targeting the agent.\n"
        "#\n"
        "# HOW IT IS USED\n"
        "#   Merge into Alertmanager config (keep existing receivers/routes).\n"
        "#   `continue: true` lets other receivers still fire. Reload AM after merge.\n"
        "#\n"
        "# CONFIGURE\n"
        "#   - Confirm webhook URL is reachable FROM Alertmanager (Docker DNS or\n"
        "#     host.docker.internal), not only from your laptop.\n"
        "#   See APPLY.md.\n"
    )
    return header + yaml.safe_dump(doc, sort_keys=False)


def _promtail_snippet(params: InstallParams) -> str:
    _ = params
    return """\
# Generated by diag install.
#
# WHAT THIS FILE DOES
#   Example Promtail (or Alloy) relabel snippet so log streams carry `service=`.
#
# HOW THE AGENT USES IT
#   Loki queries use `{service="<name>"}` from workspace/service_map.yaml.
#   Missing/mismatched labels => empty log evidence in diagnoses.
#
# CONFIGURE
#   - Align the `service` label with service_map.yaml keys and alert labels.
#   - Adapt docker_sd / static scrapes to your collector (Promtail, Alloy, ...).
#   See APPLY.md and docs/WORKSPACE.md.
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

## What this is for

Optional org annotations when a diagnosis completes. The agent POSTs to Grafana
using `AGENT_GRAFANA_URL` + `AGENT_GRAFANA_TOKEN` from `agent/.env`.

## How the agent uses it

On a finished diagnosis, if annotations are enabled, a short hypothesis summary
is written as a Grafana annotation (filterable by tag). Audit JSON and email
still work without a token.

## Configure / provision a token

Annotations require a service-account token with org-level annotation write
access (Editor on Grafana OSS).

Prometheus / Loki datasources should already exist if Grafana is in use.

1. Grafana -> Administration -> Service accounts -> Add service account
   - Name: `diagnostic-agent`
   - Role: Editor (OSS minimum for org annotations)
2. Add token -> copy once into `agent/.env` as `AGENT_GRAFANA_TOKEN=...`
3. Set `AGENT_GRAFANA_ANNOTATIONS_ENABLED=true` and restart the agent.

Detected Grafana URL: `{params.grafana_url or "(not detected)"}`

Full reference: docs/INSTALL.md (Grafana annotations section).
"""


def _host_loki_url_for_docs(params: InstallParams) -> str:
    """Host-reachable Loki URL for APPLY.md eval commands (not container DNS)."""
    raw = (params.loki_url or "").strip()
    if not raw:
        return "http://127.0.0.1:3100"
    # Container DNS / docker network names are not reachable from the host CLI.
    lowered = raw.lower()
    if "://" in lowered:
        host = lowered.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]
        if host not in {"127.0.0.1", "localhost", "0.0.0.0"} and "." not in host:
            # bare service name like loki / publishi-loki → published host port
            return "http://127.0.0.1:3100"
    return raw.rstrip("/")


def _testing_section_lines(params: InstallParams) -> list[str]:
    """Unix (bash) + Windows PowerShell copy-paste examples for APPLY.md."""
    port = params.agent_host_port
    loki_host = _host_loki_url_for_docs(params)
    live_url = f"http://127.0.0.1:{port}"
    alert_json = (
        '{"alerts":[{"status":"firing","labels":'
        '{"alertname":"HighErrorRate","service":"platform-service",'
        '"severity":"warning"},"annotations":{"summary":"test"}}]}'
    )
    return [
        "## Testing (health, alert, blind eval)",
        "",
        "Run these from the **install output root** (the directory that contains",
        "`agent/` and `APPLY.md`). Examples are shown for **Unix/macOS (bash)** and",
        "**Windows PowerShell**. If `diag` is not on PATH, use `python -m app.cli`",
        "from a checkout of this repo (common on Windows).",
        "",
        "### Health",
        "",
        "**bash**",
        "",
        "```bash",
        f"curl -sf {live_url}/health",
        "# expect: status=ok, preset matches agent.yaml extends, redaction_rules > 0",
        "```",
        "",
        "**PowerShell** (use `curl.exe` — `curl` is an alias for `Invoke-WebRequest`)",
        "",
        "```powershell",
        f"curl.exe -s {live_url}/health",
        "# expect: status=ok, preset matches agent.yaml extends, redaction_rules > 0",
        "```",
        "",
        "### Manual test alert",
        "",
        "Adjust `alertname` / `service` to labels from your live Alertmanager.",
        "Confirm the response evidence uses **this** stack's PromQL / LogQL /",
        "container hostnames (not empty queries).",
        "",
        "**bash**",
        "",
        "```bash",
        f"curl -s -X POST {live_url}/alert -H 'Content-Type: application/json' \\",
        f"  -d '{alert_json}'",
        "```",
        "",
        "**PowerShell** (write JSON to a temp file to avoid quoting issues)",
        "",
        "```powershell",
        f"$alert = '{alert_json}'",
        'Set-Content -Path "$env:TEMP\\diag-alert.json" -Value $alert -NoNewline -Encoding ascii',
        f'curl.exe -s -X POST {live_url}/alert -H "Content-Type: application/json" '
        '--data-binary "@$env:TEMP\\diag-alert.json"',
        "```",
        "",
        "### Blind eval (self-contained — no host monorepo)",
        "",
        "The workspace includes `blind_eval.yaml`. Results default to",
        "`agent/workspace/eval-results/`. See `eval/README.md` for scoring details.",
        "",
        "**bash**",
        "",
        "```bash",
        "# Offline smoke (LLM creds on the host; no agent/Loki required):",
        "diag eval -w ./agent/workspace blind --limit 3",
        "#   or: python -m app.cli eval -w ./agent/workspace blind --limit 3",
        "",
        "# Live smoke — push case logs to Loki, POST /alert on this agent:",
        "diag eval -w ./agent/workspace blind \\",
        f"  --live-url {live_url} \\",
        f"  --loki-url {loki_host} \\",
        "  --limit 3",
        "",
        "# Single case:",
        "diag eval -w ./agent/workspace blind \\",
        f"  --live-url {live_url} --loki-url {loki_host} \\",
        "  --only redis-connection",
        "",
        "# Full dataset + LLM judge:",
        "diag eval -w ./agent/workspace blind \\",
        f"  --live-url {live_url} --loki-url {loki_host} \\",
        "  --judge",
        "```",
        "",
        "**PowerShell** (backtick `` ` `` continues the line)",
        "",
        "```powershell",
        "# Offline smoke:",
        "python -m app.cli eval -w ./agent/workspace blind --limit 3",
        "",
        "# Live smoke:",
        "python -m app.cli eval -w ./agent/workspace blind `",
        f"  --live-url {live_url} `",
        f"  --loki-url {loki_host} `",
        "  --limit 3",
        "",
        "# Single case:",
        "python -m app.cli eval -w ./agent/workspace blind `",
        f"  --live-url {live_url} --loki-url {loki_host} `",
        "  --only redis-connection",
        "",
        "# Full dataset + LLM judge:",
        "python -m app.cli eval -w ./agent/workspace blind `",
        f"  --live-url {live_url} --loki-url {loki_host} `",
        "  --judge",
        "```",
        "",
        "Note: `-w` / `--workspace` is an argument of `diag eval`, **before**",
        "`blind`. Flags after `blind` (`--live-url`, `--limit`, …) go to the",
        "evaluator. From a monorepo checkout root, point `-w` at the install",
        "bundle path (e.g. `./deploy/<name>/agent/workspace`).",
        "",
        "Prereqs for **live** mode: agent healthy on the port above, Loki",
        f"reachable at `{loki_host}`, and (for offline) chat/embed credentials",
        "on the host matching `agent/.env`.",
        "",
    ]


def _apply_md(params: InstallParams, report: DiscoveryReport) -> str:
    port = params.agent_host_port
    live_url = f"http://127.0.0.1:{port}"
    lines = [
        "# Apply instructions",
        "",
        "Generated by `diag install`. Review files, then apply in order.",
        "",
        "If this bundle was generated on another machine, copy it to the runtime",
        "host first — see **Deploy the install bundle to a remote host** and",
        "**Run the agent (Docker image or standalone process)** in",
        "[docs/INSTALL.md](https://github.com/mskrado/diagnostic-agent/blob/devel/docs/INSTALL.md).",
        "",
        "## 1. Agent",
        "",
        "```bash",
        "cd agent",
        "docker compose --env-file .env up -d",
        f"# health: curl -sf {live_url}/health",
        "```",
        "",
        "```powershell",
        "cd agent",
        "docker compose --env-file .env up -d",
        f"# health: curl.exe -s {live_url}/health",
        "```",
        "",
        "Standalone (no Docker): set `AGENT_WORKSPACE` to `./workspace` (or the",
        "absolute path), load `agent/.env`, then `diag serve --host 0.0.0.0 --port 8000`.",
        "",
        "Validate the workspace without an LLM:",
        "",
        "**bash**",
        "",
        "```bash",
        'docker run --rm -v "$PWD/agent/workspace:/workspace:ro" '
        f"{params.agent_image} sh -c 'diag validate && diag lint'",
        "```",
        "",
        "**PowerShell**",
        "",
        "```powershell",
        'docker run --rm -v "${PWD}/agent/workspace:/workspace:ro" '
        f"{params.agent_image} sh -c 'diag validate && diag lint'",
        "```",
        "",
        *_testing_section_lines(params),
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
        "```powershell",
        "curl.exe -X POST http://<prometheus>/-/reload",
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
