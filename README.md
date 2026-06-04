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
| `AGENT_GRAFANA_TOKEN` | — | Viewer + `annotations:write`; empty disables Grafana |
| `AGENT_RAG_ENABLED` | `true` | RAG degrades gracefully if off/empty |

## Run with the observability overlay

```bash
# OpenAI backend (fast, recommended on Windows/macOS dev):
DIAGNOSTIC_AGENT_LLM_PROVIDER=openai \
docker compose -f docker-compose.yml -f docker-compose.observability.yml \
  --profile log-collector up -d diagnostic-agent

# Fully on-prem (Linux/GPU): add the local LLM and pull models
docker compose -f docker-compose.yml -f docker-compose.observability.yml \
  --profile local-llm --profile log-collector up -d ollama diagnostic-agent
docker exec publishi-ollama ollama pull mistral:7b-instruct
docker exec publishi-ollama ollama pull nomic-embed-text
```

> The agent reads logs from Loki, which is populated by **Promtail** — start the
> `log-collector` profile (and on Windows note the Docker-socket caveat in the
> observability architecture doc).

The agent listens on host port **8001** (`/alert`, `/health`). Alertmanager is
already wired (`infrastructure/docker/alertmanager/alertmanager-dev.yml`) to POST
firing `warning`/`critical` alerts to `http://diagnostic-agent:8000/alert`.

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
