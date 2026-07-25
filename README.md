# diagnostic-agent

A **config-driven**, reactive diagnostic agent for Prometheus / Alertmanager.

When an alert fires, the agent pulls **metrics** (Prometheus), **logs** (Loki),
and **dependency context**, retrieves relevant **runbooks** (RAG), reasons with
a pluggable LLM, and emits a structured diagnostic report.

**Hypotheses only — no auto-remediation.**

Integrating into *any* project means supplying an **integration profile**
(YAML + runbooks) and environment variables — **not** forking this codebase.

## Quick start (hello-world profile)

```bash
pip install -e ".[dev]"

export AGENT_PROFILE_DIR=$PWD/examples/hello-world
export AGENT_DEFAULT_PRESET=generic-prometheus
export AGENT_PROMETHEUS_URL=http://localhost:9090
export AGENT_LOKI_URL=http://localhost:3100
export AGENT_RAG_ENABLED=true
# LLM — pick one provider (see Configuration)
export AGENT_CHAT_PROVIDER=ollama
export AGENT_CHAT_MODEL=mistral:7b-instruct

diagnostic-agent health-check
diagnostic-agent serve --port 8000
```

Docker:

```bash
docker build -t diagnostic-agent:local .
docker run --rm -p 8001:8000 \
  -e AGENT_PROFILE_DIR=/profile \
  -e AGENT_DEFAULT_PRESET=generic-prometheus \
  -e AGENT_PROMETHEUS_URL=http://host.docker.internal:9090 \
  -e AGENT_LOKI_URL=http://host.docker.internal:3100 \
  -v "$PWD/examples/hello-world:/profile:ro" \
  diagnostic-agent:local
```

POST a synthetic alert:

```bash
curl -X POST http://localhost:8000/alert -H 'Content-Type: application/json' -d '{
  "alerts": [{
    "status": "firing",
    "labels": {"alertname": "HighErrorRate", "service": "app", "severity": "warning"}
  }]
}'
```

## Integration profile

Point `AGENT_PROFILE_DIR` at a directory containing:

| File | Purpose |
|---|---|
| `service_map.yaml` | Topology / blast radius |
| `metrics_profile.yaml` | PromQL templates (`{service}`, `{window}`) |
| `logs_profile.yaml` | Loki label, level filter, alert line filters, optional module regex |
| `redaction.yaml` | Ordered regex redaction rules |
| `prompt_profile.yaml` | Platform description + tool-run hints |
| `runbooks/` | Optional RAG corpus (markdown) |

Config precedence: **env vars > profile files > built-in presets**.

Built-in presets (shipped in-package):

- `generic-prometheus` — community `http_requests_total` naming
- `spring-micrometer` — Spring Boot Micrometer (`http_server_requests_seconds_*`, HikariCP, JVM)

Reference integrations:

- [`examples/hello-world/`](examples/hello-world/) — minimal 3-tier app
- [`integrations/publishi/`](integrations/publishi/) — full Spring Boot modular-monolith example

See **[docs/INTEGRATING.md](docs/INTEGRATING.md)** for a complete onboarding guide.

## Architecture

```
Prometheus alert ──▶ Alertmanager ──▶ POST /alert
                                         │
                                   LangGraph:
                     detect → retrieve → rag_lookup → correlate → report
                                         │
                              audit JSONL + optional email / Grafana annotation
```

## Configuration

All settings use the `AGENT_` prefix (see `.env.example`).

| Variable | Default | Notes |
|---|---|---|
| `AGENT_PROFILE_DIR` | *(package default / empty)* | Path to integration profile |
| `AGENT_DEFAULT_PRESET` | `spring-micrometer` | Built-in preset for `extends:` chains |
| `AGENT_PROMETHEUS_URL` | `http://prometheus:9090` | |
| `AGENT_LOKI_URL` | `http://loki:3100` | |
| `AGENT_CHAT_PROVIDER` / `AGENT_CHAT_MODEL` | ollama / mistral | Any LangChain provider |
| `AGENT_EMBED_PROVIDER` / `AGENT_EMBED_MODEL` | ollama / nomic-embed-text | |
| `AGENT_RAG_ENABLED` | `true` | |
| `AGENT_SERVICE_MAP_PATH` | *(from profile)* | Override topology file |
| `AGENT_RUNBOOKS_PATH` | *(from profile or `./runbooks`)* | Override RAG corpus |

## Develop

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows
# pip install -r requirements.txt               # Unix
pytest -q
```

Blind LLM eval (optional): `python eval/run_blind_eval.py` — see `eval/README.md`.

## License

Apache-2.0 — see [LICENSE](LICENSE).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The preferred contribution is a **runbook +
eval case + scenario** that CI can lint without LLM credentials.
