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

## Contents

- [Architecture (5 layers, adapted to publishi.ai)](#architecture-5-layers-adapted-to-publishiai)
- [Layout](#layout)
- [Configuration](#configuration)
  - [RAG corpus](#rag-corpus)
- [DEV quickstart (observability overlay)](#dev-quickstart-observability-overlay)
  - [1. Configure the LLM backend (in the root `.env`)](#1-configure-the-llm-backend-in-the-root-env)
  - [Grafana annotations (optional, #187)](#grafana-annotations-optional-187)
  - [Alert email via Mailpit (DEV) — two configurable emails](#alert-email-via-mailpit-dev--two-configurable-emails)
  - [Where to read outputs (DEV)](#where-to-read-outputs-dev)
  - [2. Bring up the stack (one command)](#2-bring-up-the-stack-one-command)
  - [3. (Alternative) fully on-prem with a local LLM](#3-alternative-fully-on-prem-with-a-local-llm)
  - [4. Verify](#4-verify)
- [Smoke test (#188)](#smoke-test-188)
- [Runbook scenario E2E](#runbook-scenario-e2e)
- [Try it without an alert](#try-it-without-an-alert)
- [Tests](#tests)
- [Deploy to production (EC2)](#deploy-to-production-ec2)
  - [Release the image (devel → main)](#release-the-image-devel--main)
  - [Run on the EC2 host](#run-on-the-ec2-host)
  - [Run with AWS Bedrock (no on-host Ollama)](#run-with-aws-bedrock-no-on-host-ollama)
  - [PROD smoke test](#prod-smoke-test)
  - [Rollout checklist](#rollout-checklist)
- [Compliance](#compliance)

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
| `AGENT_CHAT_PROVIDER` | `bedrock_converse` (DEV/PROD default) | LangChain provider string (`bedrock_converse`, `openai`, `ollama`, `anthropic`, `google_genai`, …) |
| `AGENT_CHAT_MODEL` | `amazon.nova-micro-v1:0` | Chat model ID for the provider |
| `AGENT_EMBED_PROVIDER` | `bedrock` (DEV/PROD default) | Embeddings provider (`bedrock`, `openai`, `ollama`, …) |
| `AGENT_EMBED_MODEL` | `amazon.titan-embed-text-v2:0` | Embedding model ID |
| `AGENT_CHAT_MODEL_KWARGS` | `{"region_name":"us-east-1"}` | JSON passthrough (`base_url`, `region_name`, …) |
| `AGENT_EMBED_MODEL_KWARGS` | `{"region_name":"us-east-1"}` | JSON passthrough for embeddings |
| `AGENT_LLM_TEMPERATURE` | `0.1` | Chat temperature |
| `OPENAI_API_KEY` | — | When using OpenAI override; also `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, or agent-scoped AWS creds (`DIAGNOSTIC_AGENT_AWS_*`) |
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
| **Credentials** | *(SDK env var, not `AGENT_`-prefixed)* | same | Bedrock: `DIAGNOSTIC_AGENT_AWS_*` (agent-only; not MinIO `AWS_*`). Also `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, or EC2 instance role in PROD |

The compose overlay reads these from the **root** `.env` (copy from `env.example`)
and maps `DIAGNOSTIC_AGENT_*` → the agent's `AGENT_*` settings.

#### Switch procedure (3 steps)

1. Set the four knobs for chat (and embed, if different) in the root `.env`.
2. If you changed the **embedding** provider/model, wipe the Chroma volume. Each
   embedding model emits vectors of a fixed dimension (e.g. `nomic-embed-text` =
   768, OpenAI `text-embedding-3-small` = 1536, Titan v2 = 1024). The persisted
   Chroma store is created with the *first* model's dimension, so pointing it at a
   new model makes queries fail or silently mismatch. The store is a Docker volume
   (`diagnostic_agent_chroma`, mounted at `/app/chroma_db`) that survives restarts,
   so you must delete it explicitly — the agent rebuilds it from `runbooks/` on the
   next start.

   **a. Find the exact volume name** (Compose prefixes it with the project name,
   usually the repo folder, e.g. `publishiai_` or `publishi-ai_`):

```bash
docker volume ls | Select-String chroma      # PowerShell
# docker volume ls | grep chroma             # bash/macOS/Linux
# -> local   publishiai_diagnostic_agent_chroma
```

   **b. Stop the agent, remove the volume, then continue to step 3.** A volume
   can't be removed while a container is using it, so stop first:

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml \
  --profile diagnostic-agent stop diagnostic-agent

docker volume rm publishiai_diagnostic_agent_chroma   # use the name from step 2a
```

   If `docker volume rm` reports the volume is still in use, find and remove the
   lingering container first:

```bash
docker ps -a | Select-String diagnostic-agent
docker rm -f publishi-diagnostic-agent
```

   > Alternative (nukes all diagnostic-agent volumes incl. audit logs — avoid in
   > PROD): `docker compose ... --profile diagnostic-agent down -v`.

   If you only changed the **chat** model (not embeddings), skip this step
   entirely — the vector store is unaffected.

3. Recreate the agent (force-recreate so the new env is picked up):

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml \
  --profile diagnostic-agent up -d --force-recreate diagnostic-agent
```

   On startup you should see the store rebuild in the logs:

```bash
docker logs publishi-diagnostic-agent 2>&1 | Select-String "RAG store built"
# -> RAG store built: 42 chunks from 11 docs
```

Verify the active backend in the logs:

```bash
docker logs publishi-diagnostic-agent 2>&1 | Select-String "Chat model:|Embeddings:|DiagnosticAgent ready"
# -> Chat model: provider=bedrock_converse model=amazon.nova-micro-v1:0
#    Embeddings: provider=bedrock model=amazon.titan-embed-text-v2:0
```

#### Example A — AWS Bedrock (DEV/PROD default: Nova Micro + Titan)

Chat uses the Converse API (`bedrock_converse`); embeddings use `bedrock`.
Compose defaults match PROD. Use **agent-only** AWS keys so MinIO keeps working:

```bash
DIAGNOSTIC_AGENT_CHAT_PROVIDER=bedrock_converse
DIAGNOSTIC_AGENT_CHAT_MODEL=amazon.nova-micro-v1:0
DIAGNOSTIC_AGENT_EMBED_PROVIDER=bedrock
DIAGNOSTIC_AGENT_EMBED_MODEL=amazon.titan-embed-text-v2:0
DIAGNOSTIC_AGENT_CHAT_MODEL_KWARGS={"region_name":"us-east-1"}
DIAGNOSTIC_AGENT_EMBED_MODEL_KWARGS={"region_name":"us-east-1"}
AWS_REGION=us-east-1
# Local DEV — agent-only (do NOT set shared AWS_ACCESS_KEY_ID for Bedrock):
DIAGNOSTIC_AGENT_AWS_ACCESS_KEY_ID=AKIA...
DIAGNOSTIC_AGENT_AWS_SECRET_ACCESS_KEY=...
# DIAGNOSTIC_AGENT_AWS_SESSION_TOKEN=...   # if temporary creds
# Or named profile + compose override mounting host ~/.aws at /root/.aws:
#   DIAGNOSTIC_AGENT_AWS_PROFILE=your-profile
#   volumes: - ${DIAGNOSTIC_AGENT_AWS_CONFIG_DIR}:/root/.aws:ro
# EC2 PROD: instance role (NO keys in .env) with IAM bedrock:InvokeModel
# Prereq: IAM allows InvokeModel on Nova + Titan
# (infrastructure/iam/policy-bedrock-invoke.json).
```

#### Example B — OpenAI (chat + embeddings override)

```bash
DIAGNOSTIC_AGENT_CHAT_PROVIDER=openai
DIAGNOSTIC_AGENT_CHAT_MODEL=gpt-4o-mini
DIAGNOSTIC_AGENT_EMBED_PROVIDER=openai
DIAGNOSTIC_AGENT_EMBED_MODEL=text-embedding-3-small
OPENAI_API_KEY=sk-...                 # your OpenAI key
# Wipe Chroma when leaving Titan embeddings (dim change).
```

#### Example C — Ollama (fully on-prem, chat + embeddings)

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

#### Example D — AWS Bedrock Claude (optional chat model)

```bash
DIAGNOSTIC_AGENT_CHAT_PROVIDER=bedrock_converse
DIAGNOSTIC_AGENT_CHAT_MODEL=anthropic.claude-3-5-haiku-20241022-v2:0
DIAGNOSTIC_AGENT_EMBED_PROVIDER=bedrock
DIAGNOSTIC_AGENT_EMBED_MODEL=amazon.titan-embed-text-v2:0
DIAGNOSTIC_AGENT_CHAT_MODEL_KWARGS={"region_name":"us-east-1"}
DIAGNOSTIC_AGENT_EMBED_MODEL_KWARGS={"region_name":"us-east-1"}
AWS_REGION=us-east-1
DIAGNOSTIC_AGENT_AWS_ACCESS_KEY_ID=...
DIAGNOSTIC_AGENT_AWS_SECRET_ACCESS_KEY=...
# Anthropic may require a one-time use-case form on first invoke.
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
| **Audit JSONL** | Full report, **`llm_exchange`** (system/user prompts, RAG context, token usage), `llm_raw` | `docker exec publishi-diagnostic-agent tail /app/audit/diagnostics-<UTC-date>.jsonl` |
| **Container logs** | One-line `llm_exchange … tokens_in=… tokens_out=… rag_used=…` per diagnosis | `docker logs publishi-diagnostic-agent` |
| **Grafana annotation** | Compact hypothesis summary on dashboards | Dashboard with tag filter `diagnostic-agent` (see above) |
| **POST /alert response** | Same structured report (incl. `llm_exchange`) | Smoke test / `curl` to `:8001/alert` |
| **Alertmanager UI** | Active alerts, silences | http://localhost:9093 |

#### Retrieving prompts + tokens (RAG / cost eval)

Every diagnosis stores an `llm_exchange` object (top-level in the audit line **and**
under `report.llm_exchange` / the `/alert` JSON):

| Field | Meaning |
|---|---|
| `system_prompt` | Exact system message sent to the LLM |
| `user_prompt` | Exact human message (alert, metrics, logs, RAG slot) |
| `rag_context` | Retrieved runbook chunks (empty string if RAG off / miss) |
| `rag_used` | `true` if `rag_context` was non-empty |
| `token_usage.input_tokens` | Prompt tokens (null if provider did not report) |
| `token_usage.output_tokens` | Completion tokens |
| `token_usage.total_tokens` | Sum when available |
| `token_usage.source` | `usage_metadata` \| `response_metadata` \| `unavailable` |
| `llm_raw` (sibling field) | Exact raw model text returned |

**PowerShell — last audit line, prompts + tokens:**

```powershell
$day = [DateTime]::UtcNow.ToString("yyyy-MM-dd")
docker exec publishi-diagnostic-agent tail -n 1 "/app/audit/diagnostics-$day.jsonl" |
  python -c @"
import sys, json
r = json.loads(sys.stdin.read())
ex = r.get('llm_exchange') or {}
u = ex.get('token_usage') or {}
print('model:', r.get('chat_provider'), r.get('chat_model'))
print('tokens_in/out/total:', u.get('input_tokens'), u.get('output_tokens'), u.get('total_tokens'),
      '(source=%s)' % u.get('source'))
print('rag_used:', ex.get('rag_used'), 'rag_chars:', len(ex.get('rag_context') or ''))
print('--- SYSTEM ---'); print(ex.get('system_prompt'))
print('--- USER ---'); print(ex.get('user_prompt'))
print('--- RAG ---'); print(ex.get('rag_context') or '(none)')
print('--- llm_raw ---'); print(r.get('llm_raw'))
"@
```

**Bash / jq — tokens only across today’s file:**

```bash
docker exec publishi-diagnostic-agent sh -c \
  'cat /app/audit/diagnostics-$(date -u +%F).jsonl' |
  jq -c '{ts:.timestamp, alert:.report.alert_type,
          in:.llm_exchange.token_usage.input_tokens,
          out:.llm_exchange.token_usage.output_tokens,
          rag:.llm_exchange.rag_used}'
```

**Container log grep (cost at a glance):**

```bash
docker logs publishi-diagnostic-agent 2>&1 | findstr /C:"llm_exchange"
# or: grep llm_exchange
```

Compare RAG-on vs RAG-off by inspecting `rag_context` / `rag_used` on successive
runs (restart with `DIAGNOSTIC_AGENT_RAG_ENABLED=false` for the blind baseline).

> **Why OpenAI by default in dev?** The image default is `ollama`, but
> the `ollama` container only starts under the `local-llm` profile. On a laptop
> without a GPU, set `DIAGNOSTIC_AGENT_CHAT_PROVIDER=openai` so the agent has a
> working LLM without pulling multi-GB models.
> **DEV LLM default is Bedrock** (same Nova Micro + Titan as PROD). Set
> `DIAGNOSTIC_AGENT_AWS_*` in root `.env` (not shared MinIO `AWS_ACCESS_KEY_ID`).
> Override to OpenAI or `--profile local-llm` + Ollama when you have no AWS IAM.
> Wipe `*_diagnostic_agent_chroma` when switching embedding providers.

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

## Blind LLM eval (RAG-off)

Measures how well the LLM identifies a root cause **from logs alone, with no
runbook/RAG context** — i.e. what the model can do without the local knowledge
base. Ground truth lives in `eval/blind_eval_dataset.yaml` (independent of the
runbooks, so no answer leaks). Full guide + **CLI parameter reference**:
**`eval/README.md`**.

**Where the logs come from / how they are injected:**

| Path | Log source | Injection point |
|---|---|---|
| Production | app stdout (logback JSON) | Promtail → Loki (agent *pulls* in `retrieve`) |
| Eval **offline** (default) | `eval/blind_eval_dataset.yaml` | straight into the prompt; RAG context hard-set to `none` |
| Eval **live** (`--live-url`) | `eval/blind_eval_dataset.yaml` | Loki push API → agent *pulls* (start agent with `AGENT_RAG_ENABLED=false`) |

**CLI flags** (`python eval/run_blind_eval.py -h`):

| Flag | Description |
|---|---|
| `--dataset PATH` | Case YAML (default `eval/blind_eval_dataset.yaml`) |
| `--out DIR` | Result JSON directory (default `eval/results/`) |
| `--only IDS` | Comma-separated case ids (e.g. `jvm-heap-oom`) |
| `--limit N` | First N cases after `--only` (`0` = all) |
| `--judge` | Extra LLM grades primary hypothesis vs ground-truth root cause |
| `--live-url URL` | Live mode: `POST {URL}/alert` (e.g. `http://localhost:8001`) |
| `--loki-url URL` | Live mode: push case logs to Loki first (e.g. `http://localhost:3100`) |

```bash
cd diagnostic-agent

# Offline (no stack needed; uses the configured AGENT_CHAT_* provider)
python eval/run_blind_eval.py
python eval/run_blind_eval.py --judge                       # + 0-5 LLM-as-judge score
python eval/run_blind_eval.py --only jvm-heap-oom           # single case
python eval/run_blind_eval.py --only redis-connection --judge

# Live full pipeline: push logs into Loki, fire /alert, read the diagnosis
DIAGNOSTIC_AGENT_RAG_ENABLED=false docker compose -f docker-compose.yml \
  -f docker-compose.observability.yml --profile diagnostic-agent up -d --force-recreate diagnostic-agent
python eval/run_blind_eval.py --live-url http://localhost:8001 --loki-url http://localhost:3100
python eval/run_blind_eval.py --only jvm-heap-oom \
  --live-url http://localhost:8001 --loki-url http://localhost:3100 --judge
```

Reports `identified_accuracy`, `mean_keyword_recall`, per-case `grounded`
(anti-hallucination) and `confidence_note` (calibration), plus judge scores with
`--judge`. Results are written to `eval/results/` (gitignored). Run RAG-off vs
RAG-on to quantify how much the runbooks help. See `eval/README.md` for scoring
details and every parameter with examples.

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

## Deploy to production (EC2)

In PROD the agent runs as an **observability sidecar** on EC2 from an **ECR
image** (not a local `build:`), with **Bedrock** (Nova Micro + Titan) as the
default LLM backend. Two stages: (1) publish the image via the release pipeline,
(2) run it on the EC2 host. Full reference: `docs/deployment/DIAGNOSTIC_AGENT_PROD.md`.

| | DEV (Docker Compose) | PROD (EC2) |
|---|---|---|
| Image | local `build: ./diagnostic-agent` | ECR `publishi/diagnostic-agent:<semver>` |
| LLM backend | Bedrock Nova Micro + Titan (default) | Bedrock Nova Micro + Titan (instance role) |
| Credentials | `DIAGNOSTIC_AGENT_AWS_*` in root `.env` (not MinIO `AWS_*`) | EC2 instance IAM role |

### Release the image (devel → main)

Images are built and pushed to ECR by `.github/workflows/release.yml`, which runs
on merge to `main`. Follow the issue/branch workflow — feature PRs land on
`devel`; `main` only receives merges from `devel` behind a Release Checklist
issue.

```bash
# 1. Land your change on devel via a normal feature PR (Closes #<issue>).
# 2. Open the release PR devel -> main:
gh pr create --base main --head devel \
  --title "Release: <version> (devel → main)" \
  --body "Release checklist: #<release-issue>"

# 3. After green CI + review, merge. release.yml computes the next semver,
#    builds every service (incl. diagnostic-agent), pushes to GHCR + ECR,
#    deploys to EC2, and tags the release.
```

To build/push without a merge (coordinate to avoid duplicate deploys):

```bash
gh workflow run release.yml -f bump=patch     # or minor / major
```

### Run on the EC2 host

On `/opt/publishi` with `.env` populated from `env.aws.example` (diagnostic +
alerting keys). **Co-located** observability (default):

```bash
docker compose -f docker-compose.yml -f docker-compose.aws.yml \
  -f docker-compose.observability.yml -f docker-compose.aws-observability.yml \
  --profile log-collector --profile diagnostic-agent --profile local-llm up -d
```

First-time Ollama model pull on the host:

```bash
docker exec publishi-ollama ollama pull mistral:7b-instruct
docker exec publishi-ollama ollama pull nomic-embed-text
```

**Split EC2** (dedicated observability host) — run on the *obs* instance:

```bash
# .env must include PROD_EC2_PRIVATE_IP=<prod-private-ip>
./scripts/render-remote-obs-config.sh

docker compose \
  -f docker-compose.observability-remote.yml \
  -f docker-compose.aws-observability-remote.yml \
  --profile diagnostic-agent --profile local-llm up -d
# or from a workstation: ./scripts/ec2-deploy-obs.sh <OBS_EC2_PUBLIC_IP>
```

### Run with AWS Bedrock (no on-host Ollama)

Recommended PROD path when you don't want to host models on the EC2 instance.
Bedrock serves both chat (Converse API) and embeddings, so you **omit**
`--profile local-llm` entirely — no Ollama container, no model pulls, less RAM.

**1. Confirm models are usable in your region.** Bedrock’s **Model access** console
page is retired. Serverless foundation models are **auto-enabled on first
invoke** in each commercial region — you do not request access in the UI.
Governance is IAM / SCPs only.

Still do this once before relying on PROD:

- Pick models in **Bedrock → Model catalog** (chat + embeddings) in `AWS_REGION`.
- Optional: smoke them in the **Playground** or with `InvokeModel` / `Converse`.
- **Anthropic** (our chat model): the first invoke in an account may require
  submitting a short use-case form; complete that with an admin login before
  the agent runs, or the first diagnostic call can fail.
- Titan Embed Text v2 is an Amazon model and normally activates on first invoke
  with no extra form (subject to IAM).

**2. Grant the EC2 instance role permission.** Attach an IAM policy to the
instance profile — **no AWS keys in `.env`**; the agent uses the instance role
via the standard AWS credential chain:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "DiagnosticAgentBedrockInvoke",
    "Effect": "Allow",
    "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
    "Resource": [
      "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-5-haiku-20241022-v2:0",
      "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0"
    ]
  }]
}
```

**3. Set the provider knobs in `/opt/publishi/.env`** (replacing the Ollama
defaults from `env.aws.example`):

```bash
DIAGNOSTIC_AGENT_CHAT_PROVIDER=bedrock_converse
DIAGNOSTIC_AGENT_CHAT_MODEL=anthropic.claude-3-5-haiku-20241022-v2:0
DIAGNOSTIC_AGENT_EMBED_PROVIDER=bedrock
DIAGNOSTIC_AGENT_EMBED_MODEL=amazon.titan-embed-text-v2:0
DIAGNOSTIC_AGENT_CHAT_MODEL_KWARGS={"region_name":"us-east-1"}
DIAGNOSTIC_AGENT_EMBED_MODEL_KWARGS={"region_name":"us-east-1"}
AWS_REGION=us-east-1
# No AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY — the instance role supplies creds.
```

**4. Bring up the stack without `local-llm`** (co-located shown; drop the two
observability overlays for the split-EC2 obs host as above):

```bash
docker compose -f docker-compose.yml -f docker-compose.aws.yml \
  -f docker-compose.observability.yml -f docker-compose.aws-observability.yml \
  --profile log-collector --profile diagnostic-agent up -d
```

**5. Chroma dimension caveat.** Titan v2 embeddings are **1024-dim** vs Ollama
`nomic-embed-text` **768-dim**. If the host previously ran with Ollama
embeddings, the persisted Chroma store has the old dimension and queries will
fail or mismatch — wipe the volume so the agent rebuilds it from `runbooks/`:

```bash
docker volume ls | grep chroma          # find the prefixed name
docker compose ... stop diagnostic-agent
docker volume rm <project>_diagnostic_agent_chroma
docker compose ... up -d --force-recreate diagnostic-agent
```

Verify the active backend in the logs:

```bash
docker logs publishi-diagnostic-agent 2>&1 | grep -E "Chat model:|Embeddings:|RAG store built"
# -> Chat model: provider=bedrock_converse model=amazon.nova-micro-v1:0
#    Embeddings: provider=bedrock model=amazon.titan-embed-text-v2:0
```

> Bedrock config background and the equivalent DEV walkthrough live in
> [Example A](#example-a--aws-bedrock-devprod-default-nova-micro--titan).

Alertmanager Slack/PagerDuty secrets are **files** (no env expansion) — create
them on the host before starting, or Alertmanager will not boot:

```bash
printf '%s' '<slack-webhook-url>'    > infrastructure/docker/alertmanager/secrets/slack_url
printf '%s' '<pagerduty-routing-key>' > infrastructure/docker/alertmanager/secrets/pagerduty_key
chmod 600 infrastructure/docker/alertmanager/secrets/*
```

### PROD smoke test

From a workstation with an SSH tunnel to the agent (obs host in split mode):

```powershell
ssh -L 8001:127.0.0.1:8001 ec2-user@<EC2_HOST>
./scripts/diagnostic-agent-smoke-test.ps1 -AgentUrl http://localhost:8001
```

Expect: `/health` 200, synthetic alert accepted, audit line written, Grafana
annotation when a token is configured.

### Rollout checklist

- [ ] ECR image published via `release.yml` (or `deploy-ec2-manual.sh`)
- [ ] **LLM backend ready:** Ollama models pulled on EC2, **or** Bedrock IAM
      (`bedrock:InvokeModel`) on the instance role + Anthropic use-case accepted
      if this is the account’s first Anthropic invoke
- [ ] `.env` populated from `env.aws.example` (diagnostic + alerting keys); for
      Bedrock, provider knobs set to `bedrock_converse`/`bedrock` + `AWS_REGION`
- [ ] Chroma volume wiped if the embedding model/dimension changed
- [ ] Alertmanager `slack_url` / `pagerduty_key` secret files present
- [ ] Stack up with `diagnostic-agent` profile (`+ local-llm` only for Ollama)
- [ ] Fire a test alert or run the smoke script; confirm Slack/PagerDuty routes

## Compliance

See `docs/architecture/DIAGNOSTIC_AGENT_SYSTEM_CARD.md` for the NIST-aligned
Agent System Card (model version, temperature, prompt, failure modes, least
privilege, auditability).
