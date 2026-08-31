# Integrating diagnostic-agent into your project

> **Installing?** Go to **[INSTALL.md](INSTALL.md)**. It is the single install
> and upgrade guide: private copy, `diag init`, start, wire, verify, upgrade.
>
> This page is the **manual** path — workspace and Alertmanager wiring for hosts
> that do not use the generator, plus the CI guard that applies to any layout.
> Prefer [`diag scan`](SCAN.md) → [`diag draft`](DRAFT.md) to bootstrap the
> YAML, then keep [`diag drift`](DRIFT.md) in CI so coverage stays honest.

---

## Topics

1. [Bootstrap from live evidence](#1-bootstrap-from-live-evidence)
2. [Create a workspace by hand](#2-create-a-workspace-by-hand)
3. [Wire Alertmanager](#3-wire-alertmanager)
4. [Docker Compose snippet](#4-docker-compose-snippet)
5. [Standalone process (`diag serve`)](#5-standalone-process-diag-serve)
6. [Verify](#6-verify)
7. [Guard the workspace in CI](#7-guard-the-workspace-in-ci)
8. [Reference: Spring Boot modular monolith](#reference-spring-boot-modular-monolith)

---

## 1. Bootstrap from live evidence

If Prometheus and Loki are reachable, do not invent the first `service_map` /
profiles from memory. Point `diag` at the stack, stage a draft, then edit:

```bash
# From a checkout that has the diag CLI (or via the published image)
export AGENT_PROMETHEUS_URL=http://prometheus:9090
export AGENT_LOKI_URL=http://loki:3100

diag scan -w infrastructure/diagnostic-agent --out ./scan-evidence.json \
  --alertmanager-url http://alertmanager:9093

diag draft -w infrastructure/diagnostic-agent \
  --bundle ./scan-evidence.json --out ./diag-draft

diag validate -w ./diag-draft && diag lint -w ./diag-draft
# Diff ./diag-draft into infrastructure/diagnostic-agent/, commit what you keep
```

| Command | Role |
|---|---|
| [`diag scan`](SCAN.md) | Read-only evidence (services, naming, alerts, log shape) |
| [`diag draft`](DRAFT.md) | Verified workspace files (optional `--llm` for prompt/runbook drafts) |
| [`diag drift`](DRIFT.md) | Ongoing gate: workspace still matches the stack |

Full install-path recipe (client fork): [INSTALL.md §5](INSTALL.md#5-customize-your-workspace).
When you cannot reach the stack yet, copy an example and edit by hand below.

---

## 2. Create a workspace by hand

Copy `examples/hello-world/` into your repository and edit:

```text
infrastructure/diagnostic-agent/
  agent.yaml             # schema, pinned agent version, preset
  service_map.yaml       # your services + dependencies
  metrics_profile.yaml   # extends: generic-prometheus | spring-micrometer
  logs_profile.yaml      # Loki label + optional alert line filters
  redaction.yaml         # tenant / PII scrubbing
  prompt_profile.yaml    # how the LLM should describe your stack
  runbooks/              # markdown playbooks for RAG
  scenarios.yaml         # alert -> runbook pairs, for `diag lint` / `diag e2e` / `diag replay`
  blind_eval.yaml        # synthetic cases for `diag eval blind`
  execution_profile.yaml # optional: allowlisted sandbox actions (omit to stay advisory-only)
```

Larger hosts move the profile YAMLs into a `profile/` subdirectory. Both
layouts resolve automatically — see [WORKSPACE.md](WORKSPACE.md) for the
**file-by-file** reference (purpose, how the agent uses each file, and how to
configure it). The subsections below are a short integrating cheat-sheet.

```yaml
# agent.yaml
schema: 1
agent_version: 0.1.0
extends: generic-prometheus
```

### service_map.yaml

```yaml
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
    log_services: [app]   # logs emitted by app, not a postgres process
module_dependencies: {}
```

`kind` selects which PromQL probes run (from your metrics profile). Presets ship
naming conventions only, so topology always comes from your profile — without a
`service_map.yaml` the agent runs with an empty dependency map (no blast radius).

### metrics_profile.yaml

Prefer extending a built-in preset:

```yaml
extends: spring-micrometer   # or generic-prometheus
```

Or override individual templates (`{service}` / `{window}` placeholders):

```yaml
extends: generic-prometheus
templates:
  error_rate: >-
    sum(rate(http_requests_total{{service="{service}",code=~"5.."}}[{window}]))
    / clamp_min(sum(rate(http_requests_total{{service="{service}"}}[{window}])), 0.001)
```

### logs_profile.yaml

```yaml
extends: generic-prometheus
service_label: service
level_filter: "ERROR|WARN"
module_regex: null          # e.g. 'c\.p\.([a-z]+)' for Spring packages
alert_line_filters:
  PostgresErrorsInLogs: "(?i)(postgres|connection).*(refused|timeout)"
```

### redaction.yaml

Rules **accumulate** along the `extends:` chain, so your tenant/PII rules are
appended to the base preset's secret scrubbing rather than replacing it. Reuse a
parent rule's `name` to override it.

```yaml
extends: generic-prometheus   # keeps bearer_token + aws_access_key
rules:
  - name: tenant_kv
    pattern: '("?tenant[_-]?id"?\s*[:=]\s*")[^"]*(")'
    replacement: '\1[REDACTED]\2'
    flags: IGNORECASE
```

Redaction is **fail-closed**: the agent refuses to start when the resolved
profile has zero rules, so a mis-pointed workspace cannot silently emit raw
data. Check the count with `diag validate` or `GET /health`;
`AGENT_REQUIRE_REDACTION=false` opts out.

> Docker creates an empty directory when a mount source is missing. The
> fail-closed guard exists for exactly that case — an empty `/workspace` falls
> back to preset redaction rather than to none.

### prompt_profile.yaml

Put a short description of *your* platform and copy-pasteable tool hints here.
Core safety rules (hypotheses-only, evidence grounding, JSON schema) stay in
agent code and cannot be overridden. For a full coding-agent checklist
(inventory of alert labels vs compose services vs container names, golden
commands, forbidden inventions), see
[`PROMPT_PROFILE_AUTHORING.md`](PROMPT_PROFILE_AUTHORING.md).

## 3. Wire Alertmanager

```yaml
receivers:
  - name: diagnostic-agent
    webhook_configs:
      - url: http://diagnostic-agent:8000/alert
        send_resolved: false
route:
  routes:
    - matchers: [severity =~ "warning|critical"]
      receiver: diagnostic-agent
```

## 4. Docker Compose snippet

```yaml
services:
  diagnostic-agent:
    image: ghcr.io/mskrado/diagnostic-agent:latest
    # For local iteration against this repo:
    # build: ./diagnostic-agent
    ports:
      - "8001:8000"
    environment:
      AGENT_PROMETHEUS_URL: http://prometheus:9090
      AGENT_LOKI_URL: http://loki:3100
      AGENT_CHAT_PROVIDER: ${AGENT_CHAT_PROVIDER:-ollama}
      AGENT_CHAT_MODEL: ${AGENT_CHAT_MODEL:-mistral:7b-instruct}
      AGENT_RAG_ENABLED: "true"
    volumes:
      - ./infrastructure/diagnostic-agent:/workspace:ro
```

The image sets `AGENT_WORKSPACE=/workspace`, so the mount is the only wiring
needed — no profile or runbook paths to keep in sync.

Full remote-copy and Compose / `docker run` recipes:
[INSTALL.md — Run the agent](INSTALL.md#run-the-agent-docker-image-or-standalone-process).

## 5. Standalone process (`diag serve`)

```bash
pip install diagnostic-agent
export AGENT_WORKSPACE=/path/to/infrastructure/diagnostic-agent
# Load AGENT_* / SDK credentials (same names as Compose env)
set -a && source /path/to/agent.env && set +a
diag serve --host 0.0.0.0 --port 8000
```

Use host-reachable Prometheus/Loki URLs (often `http://127.0.0.1:9090`), not
Docker DNS names, unless this process shares the container network namespace.
Point Alertmanager’s webhook at this host:port. See
[INSTALL.md](INSTALL.md#c-standalone-process-diag-serve) for systemd and
writable audit/Chroma paths.

## 6. Verify

```bash
curl http://localhost:8001/health
# -> {"status":"ok","profile":"diagnostic-agent","preset":"generic-prometheus",
#     "redaction_rules":3,"service_map":true,"models":{...}}
# "status":"degraded" / "redaction_rules":0 means the workspace was not found.

curl -X POST http://localhost:8001/alert -H 'Content-Type: application/json' \
  -d '{"alerts":[{"status":"firing","labels":{"alertname":"HighErrorRate","service":"app","severity":"warning"}}]}'
```

Then exercise the scenarios end to end:

```bash
docker compose exec diagnostic-agent diag e2e --url http://localhost:8000
```

For full operator smoke / remote rule-path / runbook wrappers (and what stays
host-owned vs agent-owned), see **[TESTING.md](TESTING.md)**.

## 7. Guard the workspace in CI

Neither `validate` nor `lint` needs LLM credentials or a running stack, so both
belong on every pull request that touches the workspace:

```bash
docker run --rm -v "$PWD/infrastructure/diagnostic-agent:/workspace:ro" \
  ghcr.io/mskrado/diagnostic-agent:<pinned-tag> \
  sh -c "diag validate && diag lint"
```

`validate` checks configuration (manifest schema, profile resolution, redaction
rule count, topology parse). `lint` checks content (every runbook has a scenario
and vice versa, blind-eval tokens appear in their logs, runbooks keep the
hypotheses-only framing).

Optionally gate **coverage vs the live stack** with [`diag drift`](DRIFT.md).
On a runner that can reach Prometheus:

```bash
diag drift -w infrastructure/diagnostic-agent
```

In air-gapped / GitHub-hosted CI, refresh a scan bundle on a schedule and pass
it in:

```bash
diag drift -w infrastructure/diagnostic-agent \
  --bundle ./scan-evidence.json --no-oracle
```

Client forks from `diag init` already get an optional `DRIFT_BUNDLE` step in
`client-validate.yml` — see [DRIFT.md](DRIFT.md#client-ci-hook).

## Reference: Spring Boot modular monolith

[`examples/spring-modular-monolith/`](../examples/spring-modular-monolith/) is a
complete Spring Boot modular-monolith workspace (Micrometer metrics, tenant
redaction, gateway + backing stores). Copy it and adapt the YAML for your host.
