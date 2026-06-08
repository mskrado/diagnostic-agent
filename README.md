# publishi.ai — Reactive Agentic Diagnostic Tool

A local-first **LangGraph** agent that runs the moment a Prometheus alert fires.
On an Alertmanager webhook it pulls **metrics** (Prometheus), **logs** (Loki) and
**dependency context**, reasons over them with a **local-first LLM**, retrieves
relevant **runbooks/past incidents** (RAG), and emits a structured diagnostic
report. **Hypotheses only — no auto-remediation.**

This is the agentic "brain" on top of publishi.ai's existing Grafana LGTM stack
(see `docs/architecture/OBSERVABILITY_ARCHITECTURE.md`). It is a separate Python
sidecar that deploys **only** with the observability overlay; the Java monolith
is untouched.

## Architecture (5 layers, adapted to publishi.ai)

```
Alertmanager webhook ──▶ FastAPI /alert
                              │
        LangGraph: detect ─▶ retrieve ─▶ rag_lookup ─▶ correlate ─▶ report
                              │            │              │            │
                       Prometheus +     Chroma RAG    Ollama/OpenAI   audit log
                       Loki + dep map   (runbooks)      (JSON)      + Grafana annotation
```

Key adaptations vs. the generic reference design:
- **Metric names**: Spring Micrometer (`http_server_requests_seconds_*`,
  `hikaricp_connections_*`), not the generic `http_requests_total`.
- **Loki labels**: `{service=..} | json | level="ERROR"` (Promtail promotes
  `service`/`level`/`tenantId`).
- **Topology**: modular monolith + gateway + backing stores, not a microservice
  mesh — see `service_map.yaml`.
- **Tenant safety**: tenant identifiers are redacted before any report leaves the
  agent (`app/delivery/redact.py`).

## Layout

```
diagnostic-agent/
├── app/
│   ├── config.py            # env-driven settings (AGENT_* prefix)
│   ├── llm.py               # pluggable LLM/embeddings (ollama|openai)
│   ├── dependency_map.py    # loads service_map.yaml
│   ├── clients/             # prometheus / loki / grafana + promql builders
│   ├── rag/store.py         # optional Chroma RAG over runbooks/
│   ├── graph/               # state, nodes, prompts, graph build
│   ├── delivery/            # audit JSONL, Grafana annotation, redaction
│   ├── agent.py             # wires collaborators -> compiled graph
│   └── main.py              # FastAPI /alert + /health
├── runbooks/                # RAG corpus (markdown runbooks + post-mortems)
├── service_map.yaml         # dependency / blast-radius map
├── tests/                   # pytest unit tests
└── Dockerfile
```

## Configuration

All settings are environment variables prefixed `AGENT_` (see `.env.example`).
The most important:

| Variable | Default | Notes |
|---|---|---|
| `AGENT_LLM_PROVIDER` | `ollama` | `ollama` (on-prem) or `openai` (fast/CI) |
| `AGENT_OLLAMA_MODEL` | `mistral:7b-instruct` | pulled via `ollama pull` |
| `AGENT_OPENAI_API_KEY` | — | reuse platform `OPENAI_API_KEY` |
| `AGENT_GRAFANA_TOKEN` | — | Editor (OSS DEV) or Viewer + `annotations:write` (Enterprise); empty disables Grafana |
| `AGENT_GRAFANA_ANNOTATIONS_ENABLED` | `true` | Set `false` to skip annotation delivery |
| `AGENT_RAG_ENABLED` | `true` | RAG degrades gracefully if off/empty |

## DEV quickstart (observability overlay)

The agent runs as an opt-in profile on top of the local LGTM stack. From the repo
root:

### 1. Configure the LLM backend (in the root `.env`)

The compose overlay reads these from the **root** `.env` (copy from `env.example`)
and maps them into the agent's `AGENT_*` settings:

```bash
DIAGNOSTIC_AGENT_LLM_PROVIDER=openai   # recommended on Windows/macOS dev
OPENAI_API_KEY=sk-...                  # reused as the agent's OpenAI key
# DIAGNOSTIC_AGENT_GRAFANA_TOKEN=...    # optional; see "Grafana annotations" below
```

### Grafana annotations (optional, #187)

Each diagnostic report can appear as a Grafana annotation aligned with the alert
timestamp. Provision a dedicated service-account token once:

```bash
# Grafana must be running (observability overlay). Writes gitignored .env files.
python scripts/provision_diagnostic_grafana_token.py --write-env

# Windows wrapper:
./scripts/provision-diagnostic-grafana-token.ps1 -WriteEnv
```

The script creates a `diagnostic-agent` service account, mints a token, verifies
it can POST an annotation, and sets:

- root `.env` → `DIAGNOSTIC_AGENT_GRAFANA_TOKEN` (compose injects `AGENT_GRAFANA_TOKEN`)
- `diagnostic-agent/.env` → `AGENT_GRAFANA_TOKEN` + `AGENT_GRAFANA_ANNOTATIONS_ENABLED=true`

**Token handling:** never commit real tokens. Both `.env` paths are gitignored.
Rotate by re-running the script with `--rotate` (or `-Rotate`), updating `.env`,
and restarting `diagnostic-agent`. Revoke old tokens under Grafana →
Administration → Service accounts → diagnostic-agent.

**OSS vs Enterprise:** Grafana OSS cannot attach `annotations:write` to a Viewer
service account (Enterprise RBAC). DEV uses the **Editor** basic role as
least-privilege for org-level annotations. PROD Enterprise target: Viewer +
`fixed:annotations:writer` (tracked in epic #185 Phase 4).

**Graceful degradation:** leave `DIAGNOSTIC_AGENT_GRAFANA_TOKEN` empty (or unset
`AGENT_GRAFANA_TOKEN` for standalone runs). The agent still diagnoses alerts and
writes the audit JSONL; it only skips Grafana calls (logged at INFO/WARNING).

After provisioning, restart the agent if it is already running:

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml restart diagnostic-agent
```

> **Why OpenAI by default in dev?** The image's built-in default is `ollama`, but
> the `ollama` container only starts under the `local-llm` profile. On a laptop
> without a GPU, set `DIAGNOSTIC_AGENT_LLM_PROVIDER=openai` so the agent has a
> working LLM without pulling multi-GB models.

### 2. Bring up the stack (one command)

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml \
  --profile log-collector --profile diagnostic-agent up -d
```

This starts Prometheus, Loki, **Promtail** (`log-collector`), Alertmanager,
Grafana, and the **diagnostic-agent**. Promtail is required or Loki has no logs;
on Windows note the Docker-socket caveat in
`docs/architecture/OBSERVABILITY_ARCHITECTURE.md`.

### 3. (Alternative) fully on-prem with a local LLM

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml \
  --profile log-collector --profile local-llm --profile diagnostic-agent up -d
docker exec publishi-ollama ollama pull mistral:7b-instruct
docker exec publishi-ollama ollama pull nomic-embed-text
```

Leave `DIAGNOSTIC_AGENT_LLM_PROVIDER` at its `ollama` default (or set it
explicitly) when using this path.

### 4. Verify

```bash
curl http://localhost:8001/health
# -> {"status":"ok","agent_initialized":true}

docker logs publishi-diagnostic-agent --tail 50   # confirm Prometheus/Loki reachable
```

The agent listens on host port **8001** (`/alert`, `/health`). Alertmanager is
already wired (`infrastructure/docker/alertmanager/alertmanager-dev.yml`) to POST
firing `warning`/`critical` alerts to `http://diagnostic-agent:8000/alert`.

> **Running the agent standalone** (without compose, e.g. for pytest): copy
> `cp diagnostic-agent/.env.example diagnostic-agent/.env`. Its `AGENT_*` defaults
> already point at the Docker DNS names (`prometheus:9090`, `loki:3100`,
> `grafana:3000`, `ollama:11434`).

See `docs/deployment/OBSERVABILITY_OPERATIONS_GUIDE.md` §8.6 for the operator view.

## Try it without an alert

```bash
curl -X POST http://localhost:8001/alert -H 'Content-Type: application/json' -d '{
  "alerts": [{
    "status": "firing",
    "labels": {"alertname": "HighErrorRate", "service": "platform-service", "severity": "warning"}
  }]
}'
```

The report is returned in the response, appended to `audit/diagnostics-<date>.jsonl`,
and (if a Grafana token is set) posted as a Grafana annotation.

## Tests

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m pytest -q
```

## Compliance

See `docs/architecture/DIAGNOSTIC_AGENT_SYSTEM_CARD.md` for the NIST-aligned
Agent System Card (model version, temperature, prompt, failure modes, least
privilege, auditability).
