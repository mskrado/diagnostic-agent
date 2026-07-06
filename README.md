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
Prometheus fires alert ──▶ Alertmanager
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
     diagnostic-agent    Mailpit (alert)  Mailpit (diagnostic)
         webhook           AM email         agent email
              │
        FastAPI /alert
              │
        LangGraph: detect ─▶ retrieve ─▶ rag_lookup ─▶ correlate ─▶ report
              │            │              │              │
        Prometheus +   Chroma RAG    Ollama/OpenAI   audit JSONL
        Loki + dep map (runbooks)      (JSON)        + Grafana annotation
                                                      + SMTP diagnostic email
```

Key adaptations vs. the generic reference design:
- **Metric names**: Spring Micrometer (`http_server_requests_seconds_*`,
  `hikaricp_connections_*`), not the generic `http_requests_total`.
- **Loki labels**: `{service=..} | json | level=~"ERROR|WARN"` (Promtail promotes
  `service`/`level`/`tenantId`; WARN included so smoke tests surface soft failures).
- **Topology**: modular monolith + gateway + backing stores, not a microservice
  mesh — see `service_map.yaml`.
- **Tenant safety**: tenant identifiers are redacted before any report leaves the
  agent (`app/delivery/redact.py`).

## Layout

```
diagnostic-agent/
├── app/
│   ├── config.py            # env-driven settings (AGENT_* prefix)
│   ├── llm.py               # pluggable LLM/embeddings (LangChain init_*)
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
| `AGENT_CHAT_PROVIDER` | `openai` (compose DEV) / `ollama` (PROD) | LangChain provider string (`openai`, `ollama`, `anthropic`, `google_genai`, `bedrock_converse`, …) |
| `AGENT_CHAT_MODEL` | `gpt-4o-mini` / `mistral:7b-instruct` | Chat model ID for the provider |
| `AGENT_EMBED_PROVIDER` | same family as chat | Embeddings provider (`openai`, `ollama`, `bedrock`, …) |
| `AGENT_EMBED_MODEL` | `text-embedding-3-small` / `nomic-embed-text` | Embedding model ID |
| `AGENT_CHAT_MODEL_KWARGS` | `{}` | JSON passthrough (`base_url`, `region_name`, …) |
| `AGENT_EMBED_MODEL_KWARGS` | `{}` | JSON passthrough for embeddings |
| `AGENT_LLM_TEMPERATURE` | `0.1` | Chat temperature |
| `OPENAI_API_KEY` | — | Standard SDK env (not `AGENT_`-prefixed); also `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, AWS credential chain |
| `AGENT_GRAFANA_TOKEN` | — | Editor (OSS DEV) or Viewer + `annotations:write` (Enterprise); empty disables Grafana |
| `AGENT_GRAFANA_ANNOTATIONS_ENABLED` | `true` | Set `false` to skip annotation delivery |
| `AGENT_EMAIL_ENABLED` | `true` | SMTP diagnostic email (hypotheses); separate from Alertmanager alert mail |
| `AGENT_EMAIL_TO` | `dev-alerts@localhost` | Recipient(s) for diagnostic email |
| `AGENT_SMTP_HOST` / `AGENT_SMTP_PORT` | `mailpit` / `1025` | DEV Mailpit SMTP |
| `AGENT_RAG_ENABLED` | `true` | RAG degrades gracefully if off/empty |

### RAG corpus

Runbooks live in `runbooks/` (see `runbooks/README.md`). At startup the agent:

- loads all `**/*.md` files;
- splits with **chunk_size=800**, **chunk_overlap=80**;
- retrieves **top_k=3** chunks per alert (`AGENT_RAG_TOP_K` if exposed).

Restart the container after adding runbooks so Chroma rebuilds. Quality checks:
`pytest tests/test_rag_eval.py`.

## DEV quickstart (observability overlay)

The agent runs as an opt-in profile on top of the local LGTM stack. From the repo
root:

### 1. Configure the LLM backend (in the root `.env`)

Switching models is **environment-only** — no code changes, no rebuild. The agent
uses LangChain's universal factories (`init_chat_model` / `init_embeddings`), so
any supported provider is a drop-in.

#### How switching works — four knobs

Chat and embeddings are configured independently with the same four knobs each:

| Knob | Chat var | Embed var | What it is |
|---|---|---|---|
| **Provider** | `DIAGNOSTIC_AGENT_CHAT_PROVIDER` | `DIAGNOSTIC_AGENT_EMBED_PROVIDER` | LangChain provider string (`openai`, `ollama`, `bedrock_converse`/`bedrock`, `anthropic`, `google_genai`, …) |
| **Model** | `DIAGNOSTIC_AGENT_CHAT_MODEL` | `DIAGNOSTIC_AGENT_EMBED_MODEL` | Model ID as the provider names it |
| **Kwargs** | `DIAGNOSTIC_AGENT_CHAT_MODEL_KWARGS` | `DIAGNOSTIC_AGENT_EMBED_MODEL_KWARGS` | JSON blob of extra args (`base_url`, `region_name`, …); omit/`{}` if none |
| **Credentials** | *(SDK env var, not `AGENT_`-prefixed)* | same | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, or the AWS credential chain |

The compose overlay reads these from the **root** `.env` (copy from `env.example`)
and maps `DIAGNOSTIC_AGENT_*` → the agent's `AGENT_*` settings.

#### Switch procedure (3 steps)

1. Set the four knobs for chat (and embed, if different) in the root `.env`.
2. If you changed the **embedding** provider/model, wipe the Chroma volume — the
   vector dimensions change and a stale store will error or mismatch:

```bash
docker volume rm publishi_diagnostic_agent_chroma   # exact name: docker volume ls
```

3. Recreate the agent (force-recreate so the new env is picked up):

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml \
  --profile diagnostic-agent up -d --force-recreate diagnostic-agent
```

Verify the active backend in the logs:

```bash
docker logs publishi-diagnostic-agent 2>&1 | Select-String "Chat model:|Embeddings:|DiagnosticAgent ready"
# -> Chat model: provider=openai model=gpt-4o-mini
#    Embeddings: provider=openai model=text-embedding-3-small
```

#### Example A — OpenAI (chat + embeddings)

```bash
DIAGNOSTIC_AGENT_CHAT_PROVIDER=openai
DIAGNOSTIC_AGENT_CHAT_MODEL=gpt-4o-mini
DIAGNOSTIC_AGENT_EMBED_PROVIDER=openai
DIAGNOSTIC_AGENT_EMBED_MODEL=text-embedding-3-small
OPENAI_API_KEY=sk-...                 # your OpenAI key
# no kwargs needed for stock OpenAI
```

#### Example B — Ollama (fully on-prem, chat + embeddings)

Point `base_url` at the Ollama host. Use `http://ollama:11434` with the bundled
`--profile local-llm` container, or a remote host IP.

```bash
DIAGNOSTIC_AGENT_CHAT_PROVIDER=ollama
DIAGNOSTIC_AGENT_CHAT_MODEL=mistral:7b-instruct
DIAGNOSTIC_AGENT_EMBED_PROVIDER=ollama
DIAGNOSTIC_AGENT_EMBED_MODEL=nomic-embed-text
DIAGNOSTIC_AGENT_CHAT_MODEL_KWARGS={"base_url":"http://ollama:11434"}
DIAGNOSTIC_AGENT_EMBED_MODEL_KWARGS={"base_url":"http://ollama:11434"}
# no API key required; pull models first:
#   docker exec publishi-ollama ollama pull mistral:7b-instruct
#   docker exec publishi-ollama ollama pull nomic-embed-text
```

#### Example C — AWS Bedrock (chat + embeddings)

Chat uses the Converse API (`bedrock_converse`); embeddings use `bedrock`.

```bash
DIAGNOSTIC_AGENT_CHAT_PROVIDER=bedrock_converse
DIAGNOSTIC_AGENT_CHAT_MODEL=anthropic.claude-3-5-haiku-20241022-v2:0
DIAGNOSTIC_AGENT_EMBED_PROVIDER=bedrock
DIAGNOSTIC_AGENT_EMBED_MODEL=amazon.titan-embed-text-v2:0
DIAGNOSTIC_AGENT_CHAT_MODEL_KWARGS={"region_name":"us-east-1"}
DIAGNOSTIC_AGENT_EMBED_MODEL_KWARGS={"region_name":"us-east-1"}
AWS_REGION=us-east-1
# Credentials via the standard AWS chain:
#   - Local dev: AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY (+ AWS_SESSION_TOKEN), or
#                a named profile via {"credentials_profile_name":"myprofile"} in kwargs
#   - EC2 PROD:  instance role (NO keys in .env) with IAM: bedrock:InvokeModel
# Prereq: enable model access for both model IDs in the Bedrock console (per region).
```

#### Mixing providers (chat vs embeddings)

Chat and embeddings are independent, so you can mix — e.g. a chat-only API that
has no embeddings endpoint (DeepSeek) with local Ollama embeddings:

```bash
DIAGNOSTIC_AGENT_CHAT_PROVIDER=openai
DIAGNOSTIC_AGENT_CHAT_MODEL=deepseek-chat
DIAGNOSTIC_AGENT_CHAT_MODEL_KWARGS={"base_url":"https://api.deepseek.com/v1"}
OPENAI_API_KEY=sk-...your-deepseek-key...
DIAGNOSTIC_AGENT_EMBED_PROVIDER=ollama
DIAGNOSTIC_AGENT_EMBED_MODEL=nomic-embed-text
DIAGNOSTIC_AGENT_EMBED_MODEL_KWARGS={"base_url":"http://192.168.100.7:11434"}
# Or skip embeddings entirely: DIAGNOSTIC_AGENT_RAG_ENABLED=false
```

> **Adding a brand-new provider:** install its `langchain-*` package in
> `requirements.txt` (e.g. `langchain-cohere`), then set the provider/model env
> vars. No code change to `app/llm.py` is needed.

> **Windows note:** the `Select-String` log filter above is PowerShell. On
> bash/macOS/Linux use `docker logs publishi-diagnostic-agent 2>&1 | grep -E "Chat model:|Embeddings:"`.

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

### Alert email via Mailpit (DEV) — two configurable emails

By default, firing `warning` / `critical` alerts produce **two emails** in Mailpit
(http://localhost:8025):

| Email | Sender | Subject prefix | Content |
|---|---|---|---|
| **Alert** | Alertmanager | `[FIRING:…]` (AM template) | Alert labels, summary, Prometheus context |
| **Diagnostic** | diagnostic-agent | `[publishi diagnostic]` | LLM hypotheses, evidence, blast radius, next steps |

> **Important:** Only alerts that flow through **Alertmanager** produce the alert
> email. A direct `POST` to `http://localhost:8001/alert` (or the runbook E2E
> runner) triggers the agent webhook path only — you get the **diagnostic summary
> email**, not the Alertmanager alert email. Use the Alertmanager API (`:9093`) or
> the default smoke test (no `-DirectAgent`) to exercise both emails.

Both are enabled by default. Disable either in root `.env`:

```bash
# Disable Alertmanager alert email only (webhook + agent email still run)
DIAGNOSTIC_ALERTMANAGER_EMAIL_RECEIVER=blackhole

# Disable agent diagnostic email only (Alertmanager alert email still runs)
DIAGNOSTIC_AGENT_EMAIL_ENABLED=false
```

| Variable | Default | Purpose |
|---|---|---|
| `DIAGNOSTIC_ALERTMANAGER_EMAIL_RECEIVER` | `email-dev` | `blackhole` disables AM → Mailpit |
| `DIAGNOSTIC_AGENT_EMAIL_ENABLED` | `true` | Agent SMTP diagnostic mail |
| `DIAGNOSTIC_AGENT_EMAIL_TO` | `dev-alerts@localhost` | Recipient(s), comma-separated |
| `DIAGNOSTIC_AGENT_SMTP_HOST` | `mailpit` | SMTP host (Docker DNS) |
| `DIAGNOSTIC_AGENT_SMTP_PORT` | `1025` | SMTP port |
| `DIAGNOSTIC_AGENT_SMTP_FROM` | `diagnostic-agent@publishi.local` | From address |

Alertmanager config: `infrastructure/docker/alertmanager/alertmanager-dev.yml`
(uses `entrypoint-dev.sh` for `DIAGNOSTIC_ALERTMANAGER_EMAIL_RECEIVER`). Agent maps `DIAGNOSTIC_AGENT_*` → `AGENT_*` in
`docker-compose.observability.yml`.

Ensure Mailpit is running:

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml up -d mailpit alertmanager
docker compose -f docker-compose.yml -f docker-compose.observability.yml \
  --profile diagnostic-agent up -d --force-recreate diagnostic-agent
```

**Test Alertmanager alert email** (PowerShell):

```powershell
Invoke-RestMethod -Uri "http://localhost:9093/api/v2/alerts" -Method POST -ContentType "application/json" -Body '[{"labels":{"alertname":"DevMailpitTest","service":"platform-service","severity":"warning"},"annotations":{"summary":"Alertmanager Mailpit test"},"startsAt":"2026-01-01T00:00:00.000Z"}]'
```

**Test agent diagnostic email** (needs diagnostic-agent profile + working LLM):

```powershell
Invoke-RestMethod -Uri "http://localhost:8001/alert" -Method POST -ContentType "application/json" -Body '{"alerts":[{"status":"firing","labels":{"alertname":"HighErrorRate","service":"platform-service","severity":"warning"},"annotations":{"summary":"Agent diagnostic email test"}}]}'
```

Expect the Alertmanager mail within ~30s (`group_wait`); the diagnostic mail
arrives when the agent finishes (seconds, depends on LLM).

Operator details: `docs/deployment/OBSERVABILITY_OPERATIONS_GUIDE.md` §8.3.1.

### Where to read outputs (DEV)

| Output | What it contains | Where |
|---|---|---|
| **Mailpit — alert email** | Firing alert from Alertmanager | http://localhost:8025 |
| **Mailpit — diagnostic email** | Agent hypotheses + evidence | http://localhost:8025 |
| **Audit JSONL** | Full report, metrics snapshot, log sample, LLM raw output | `docker exec publishi-diagnostic-agent tail /app/audit/diagnostics-<UTC-date>.jsonl` |
| **Grafana annotation** | Compact hypothesis summary on dashboards | Dashboard with tag filter `diagnostic-agent` (see above) |
| **POST /alert response** | Same structured report as audit (immediate) | Smoke test / `curl` to `:8001/alert` |
| **Alertmanager UI** | Active alerts, silences | http://localhost:9093 |

> **Why OpenAI by default in dev?** The image default is `ollama`, but
> the `ollama` container only starts under the `local-llm` profile. On a laptop
> without a GPU, set `DIAGNOSTIC_AGENT_CHAT_PROVIDER=openai` so the agent has a
> working LLM without pulling multi-GB models.

### 2. Bring up the stack (one command)

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml \
  --profile log-collector --profile diagnostic-agent up -d
```

This starts Prometheus, Loki, **Promtail** (`log-collector`), Alertmanager,
Grafana, and the **diagnostic-agent**. **Mailpit** (base compose) should also be
up for DEV alert emails — Alertmanager sends to `mailpit:1025` in parallel with
the agent webhook. Promtail is required or Loki has no logs; on Windows note the
Docker-socket caveat in `docs/architecture/OBSERVABILITY_ARCHITECTURE.md`.

### 3. (Alternative) fully on-prem with a local LLM

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml \
  --profile log-collector --profile local-llm --profile diagnostic-agent up -d
docker exec publishi-ollama ollama pull mistral:7b-instruct
docker exec publishi-ollama ollama pull nomic-embed-text
```

Leave `DIAGNOSTIC_AGENT_CHAT_PROVIDER=ollama` (or set it
explicitly) when using this path.

### 4. Verify

```bash
curl http://localhost:8001/health
# -> {"status":"ok","agent_initialized":true}

docker logs publishi-diagnostic-agent --tail 50   # confirm Prometheus/Loki reachable
```

The agent listens on host port **8001** (`/alert`, `/health`). Alertmanager is
already wired (`infrastructure/docker/alertmanager/alertmanager-dev.yml`) to POST
firing `warning`/`critical` alerts to `http://diagnostic-agent:8000/alert` and to
email the same alerts to **Mailpit** (`email-dev` receiver).

> **Running the agent standalone** (without compose, e.g. for pytest): copy
> `cp diagnostic-agent/.env.example diagnostic-agent/.env`. Its `AGENT_*` defaults
> already point at the Docker DNS names (`prometheus:9090`, `loki:3100`,
> `grafana:3000`, `ollama:11434`).

See `docs/deployment/OBSERVABILITY_OPERATIONS_GUIDE.md` §8.6 for the operator view.

## Smoke test (#188)

Repeatable end-to-end verification of the **Alertmanager → agent → Mailpit** loop
(both emails by default):

```bash
# Stack must be up (OpenAI key in root .env recommended for DEV):
docker compose -f docker-compose.yml -f docker-compose.observability.yml \
  --profile log-collector --profile diagnostic-agent up -d

./scripts/diagnostic-agent-smoke-test.ps1
./scripts/diagnostic-agent-smoke-test.ps1 -RealPath      # optional fault injection
./scripts/diagnostic-agent-smoke-test.ps1 -DirectAgent   # agent-only (diagnostic email)
```

The script checks `/health`, posts a synthetic alert **via Alertmanager** (so both
Mailpit emails are produced), waits for the agent audit record, verifies tenant
redaction, and optionally checks Mailpit for `alertmanager@` + `diagnostic-agent@`
messages. Use `-SkipGrafana` / `-SkipMailpit` to skip optional steps.

## Runbook scenario E2E

One synthetic Alertmanager event per `runbook-*.md` is defined in
`runbook_scenarios.yaml`. The runner POSTs each payload to `/alert` and asserts the
structured report matches the scenario labels (plus offline corpus checks).

```bash
# Live — all 11 runbook scenarios (~2–5 min with OpenAI; longer if deps are down)
./scripts/diagnostic-agent-runbook-e2e.ps1

# Offline only (no Docker): verify every runbook has a scenario + corpus tokens
./scripts/diagnostic-agent-runbook-e2e.ps1 -Mode offline

# Single scenario
./scripts/diagnostic-agent-runbook-e2e.ps1 -Scenario high-error-rate

# Pytest offline (default) + optional live E2E
cd diagnostic-agent && python -m pytest tests/test_runbook_scenarios.py -q
AGENT_E2E_URL=http://localhost:8001 python -m pytest tests/test_runbook_scenarios.py -m e2e -q
```

Add a scenario when authoring a new runbook: copy an entry in `runbook_scenarios.yaml`
with matching `alertname` / `service` labels from `docs/observability/ALERTING_STRATEGY.md`.

## Try it without an alert

**Agent only (diagnostic summary email — no Alertmanager alert email):**

```bash
curl -X POST http://localhost:8001/alert -H 'Content-Type: application/json' -d '{
  "alerts": [{
    "status": "firing",
    "labels": {"alertname": "HighErrorRate", "service": "platform-service", "severity": "warning"}
  }]
}'
```

**Both emails (Alertmanager → Mailpit alert + agent diagnostic):**

```powershell
Invoke-RestMethod -Uri "http://localhost:9093/api/v2/alerts" -Method POST -ContentType "application/json" -Body '[{"labels":{"alertname":"DevMailpitTest","service":"platform-service","severity":"warning"},"annotations":{"summary":"Manual dual-email test"},"startsAt":"2026-01-01T00:00:00.000Z"}]'
```

The report is returned in the `/alert` response (direct path only), appended to
`audit/diagnostics-<date>.jsonl`, emailed when `AGENT_EMAIL_ENABLED=true`, and (if
a Grafana token is set) posted as a Grafana annotation.

## Tests

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m pytest -q
```

## Compliance

See `docs/architecture/DIAGNOSTIC_AGENT_SYSTEM_CARD.md` for the NIST-aligned
Agent System Card (model version, temperature, prompt, failure modes, least
privilege, auditability).
