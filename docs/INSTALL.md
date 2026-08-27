# Installing diagnostic-agent

`diag install` discovers observability tools on a host, collects every parameter
the agent needs to run, and writes a complete install bundle (agent build/run
files **and** observability wiring) into a directory you choose.

```bash
pip install -e ".[dev]"   # or: pip install diagnostic-agent
diag install --output ./deploy
```

This guide covers **interactive** and **non-interactive** modes, every parameter
that is collected (why it exists, required vs optional), examples, and what to
do after generation.

Related docs: [INTEGRATING.md](INTEGRATING.md) · [WORKSPACE.md](WORKSPACE.md)

---

## Topics

1. [Modes at a glance](#modes-at-a-glance)
2. [Interactive mode](#interactive-mode)
3. [Non-interactive mode](#non-interactive-mode)
4. [CLI flags reference](#cli-flags-reference)
5. [Parameters collected (full reference)](#parameters-collected-full-reference)
6. [What gets generated](#what-gets-generated)
7. [After install](#after-install)
8. [Graceful degradation](#graceful-degradation)
9. [Troubleshooting](#troubleshooting)
10. [Requirements](#requirements)
11. [Quick recipe card](#quick-recipe-card)

---

## Modes at a glance

| Mode | When to use | How |
|---|---|---|
| **Interactive** (default) | First-time install; **confirm every parameter** after discovery (Enter keeps defaults) | `diag install --output ./deploy` |
| **Accept defaults** | Re-run against a stack you already trust; resolves interactively but never prompts | `diag install --output ./deploy --accept-defaults` |
| **Non-interactive** | CI/CD, automation, remote unattended hosts | `diag install --output ./deploy --non-interactive --yes` plus flags/env for anything discovery cannot fill |
| **Dry-run** | Preview discovery + file plan without writing | add `--dry-run` to any mode |

If stdin is not a terminal (piped input, CI shell), interactive mode switches
itself to non-interactive and says so, rather than silently accepting defaults.
`Ctrl-C` at any prompt exits cleanly with code `130` and writes nothing.

### Resolution order

Candidates are resolved in this order (first non-empty wins as the **default**):

1. **CLI flag** (e.g. `--prometheus-url`)
2. **Environment variable** (e.g. `AGENT_PROMETHEUS_URL`)
3. **Discovery** (Docker introspection + HTTP probes + port scan)

Then:

4. **Interactive mode (default):** **confirm every parameter** — each value is
   shown with the candidate as the default; Enter accepts, or type a replacement.
   Input is validated (URL shape, port range, allowed choices) and re-asked on
   error. Leaving a required field blank fails closed (unless `--allow-degraded`).
5. **Non-interactive mode:** accept candidates as-is; missing required values
   fail closed (or degrade only with `--allow-degraded`).

Secrets (API keys, Grafana token, SMTP password) are prompted with hidden input
in interactive mode and are **never** written to `install-report.json` (redacted
as `***`). They only land in `agent/.env`, which is gitignored in the bundle.

---

## Interactive mode

Best for humans standing up the agent against a live stack.

```bash
diag install --output ./deploy
```

### What you will see

1. **Discovery summary** — live status chart while probing (TTY), then the same
   table left on screen with each tool marked `OK` / `-`. Non-TTY callers get
   the static summary after discovery finishes.
2. **Confirm every parameter**, grouped into six numbered sections — discovery /
   flags / env only supply defaults:

   | Section | Covers |
   |---|---|
   | 1. Workspace preset | `generic-prometheus` vs `spring-micrometer` |
   | 2. Observability endpoints | Prometheus, Loki |
   | 3. Alert routing | Alertmanager + agent webhook URL |
   | 4. Grafana annotations | Grafana URL (optional) |
   | 5. Diagnosis LLM | provider, models, **credentials** |
   | 6. Diagnostic email | SMTP host/port/addresses, then the Grafana token |

3. **Container-reachability rewrite** — the installer probes from *your* host, so
   a discovered `http://127.0.0.1:9090` would point at the agent container once
   it runs. Interactive mode offers `http://host.docker.internal:9090` instead
   (default yes) and the generated compose adds the matching
   `host.docker.internal:host-gateway` mapping so it also works on Linux.
4. **Reachability warning** — a URL you *change* is health-checked; a failure
   warns and is recorded in `install-report.json` rather than blocking you.
5. **Review summary** — every resolved value is printed before anything is
   written. Answer `n` to walk the prompts again; `--yes` skips this step.
6. **Verify**, then **next steps** with copy-pasteable commands.

### Provider credentials are collected, not assumed

Picking a provider interactively also collects what it needs to run:

| Provider | Collected |
|---|---|
| `ollama` | base URL (with container rewrite) |
| `openai` / `anthropic` / `google` | API key (hidden input; required unless `--allow-degraded`) |
| `bedrock` | region + either explicit `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, or an explicit "use ambient credentials" answer that is recorded as a warning |

This closes the case where choosing Bedrock produced an `agent/.env` with a
Bedrock provider and no credentials.

### Interactive example

```text
$ diag install --output ./deploy

diag install - target=local output=./deploy

Discovery (6/9 reachable on local)
----------------------------------
  [OK ] alertmanager   http://127.0.0.1:9093
  [OK ] grafana        http://127.0.0.1:3000  v11.0.0
  [OK ] loki           http://127.0.0.1:3100
  [OK ] ollama         http://127.0.0.1:11434
  [OK ] prometheus     http://127.0.0.1:9090
  [ - ] mailpit        (not found)
  placement: standalone_local

[1/6] Workspace preset
-----------------------
  Selects PromQL templates and log label conventions.
Metrics/logs preset [generic-prometheus/spring-micrometer] [generic-prometheus]:

[2/6] Observability endpoints (agent reads these)
--------------------------------------------------
  Required: metrics are the primary diagnosis signal.
Prometheus URL [http://127.0.0.1:9090]:
  Loopback addresses point at the agent container, not your host.
The agent runs in a container; use http://host.docker.internal:9090 instead? [Y/n]:
...

Review
------
  preset         generic-prometheus
  prometheus     http://host.docker.internal:9090
  loki           http://host.docker.internal:3100
  alertmanager   http://host.docker.internal:9093
  webhook        http://host.docker.internal:8001/alert
  grafana        http://host.docker.internal:3000
  grafana token  (none)
  chat           ollama/mistral:7b-instruct
  embeddings     ollama/nomic-embed-text
  email          (disabled)
Write the install bundle with these settings? [Y/n]:

Wrote 21 file(s)
  ...

verify OK

Next steps
----------
  1. Review   deploy/install-report.json
  2. Edit     deploy/agent/workspace/service_map.yaml
  3. Start    cd deploy/agent && docker compose --env-file .env up -d
  4. Health   curl -sf http://127.0.0.1:8001/health
  5. Wire     merge deploy/observability into your live stack
```

### Interactive + remote discovery

```bash
diag install \
  --target ops.example.com \
  --ssh ec2-user@ops.example.com \
  --output ./deploy-ops
```

- `--target` — host (or base URL) used for HTTP probes / published ports.
- `--ssh` — richer topology: remote `docker ps` for container names, networks,
  and published ports. Without SSH, HTTP + port scan still work (lower
  confidence, noted in the report).

---

## Non-interactive mode

Never prompts. Anything discovery cannot supply must come from **flags** or
**environment variables**. Use for pipelines and scripted rollouts.

```bash
diag install \
  --output ./deploy \
  --non-interactive \
  --yes \
  --prometheus-url http://prometheus:9090 \
  --loki-url http://loki:3100 \
  --grafana-url http://grafana:3000 \
  --alertmanager-url http://alertmanager:9093 \
  --preset spring-micrometer \
  --chat-provider bedrock_converse \
  --chat-model amazon.nova-micro-v1:0
```

### Behaviour when something is missing

Default is **fail closed** (matches the install contract: every parameter needed
to run + complete observability wiring). Soft-degrade requires `--allow-degraded`.

| Gap | Default behaviour | With `--allow-degraded` |
|---|---|---|
| No Prometheus URL (flag/env/discovery) | **Exit 1** | **Exit 1** |
| No Loki | **Exit 1** | Continue; metrics-only diagnosis |
| No Alertmanager | **Exit 1** | Continue; no webhook route generated |
| No LLM credentials / reachable Ollama | **Exit 1** (non-interactive) | Default to **ollama** + warning |
| No Grafana / no token | Continue; annotations disabled | Same (optional delivery) |
| No Mailpit / SMTP | Email delivery **disabled** | Same (optional delivery) |

### CI-friendly pattern

```bash
export AGENT_PROMETHEUS_URL=http://prometheus:9090
export AGENT_LOKI_URL=http://loki:3100
export AGENT_GRAFANA_URL=http://grafana:3000
export AGENT_GRAFANA_TOKEN="glsa_..."          # optional
export OPENAI_API_KEY="sk-..."                 # or AWS_* for Bedrock
export AWS_REGION=us-east-1

diag install --output ./deploy --non-interactive --yes --dry-run   # preview
diag install --output ./deploy --non-interactive --yes             # write
# optional:
diag install --output ./deploy --non-interactive --yes --apply --start
```

`--yes` skips the confirmation prompt that `--apply` would otherwise show before
reloading a live Prometheus/Alertmanager.

---

## CLI flags reference

| Flag | Required? | Default | Purpose |
|---|---|---|---|
| `--output` / `-o` | **Yes** | — | Directory for the install bundle |
| `--target` | No | `local` | Host or base URL to probe (`local`, hostname, or `http://host`) |
| `--ssh USER@HOST` | No | — | SSH for remote Docker introspection (BatchMode; key auth) |
| `--preset` | No | `auto` | `auto` \| `generic-prometheus` \| `spring-micrometer` |
| `--prometheus-url` | Conditionally | discovered | Override Prometheus base URL |
| `--loki-url` | No | discovered | Override Loki base URL |
| `--grafana-url` | No | discovered | Override Grafana base URL |
| `--alertmanager-url` | No | discovered | Override Alertmanager base URL |
| `--webhook-url` | No | from reachability matrix | Alertmanager → agent webhook URL |
| `--chat-provider` | No | auto | Force LLM provider |
| `--chat-model` | No | provider default | Force chat model id |
| `--timeout` | No | `3` | HTTP probe timeout (seconds) |
| `--dry-run` | No | off | Print plan; write nothing |
| `--force` | No | off | Allow replacing differing files (always keeps `*.bak.<utc>` backups) |
| `--non-interactive` | No | off | Never prompt |
| `--accept-defaults` | No | off | Resolve interactively but accept every default without prompting |
| `--allow-degraded` | No | off | Permit metrics-only / no AM webhook / blind Ollama fallback; default is fail closed |
| `--yes` / `-y` | No | off | Skip the review confirmation and the `--apply` prompt |
| `--apply` | No | off | Best-effort `POST /-/reload` on Prometheus & Alertmanager |
| `--start` | No | off | `docker compose up -d` in `agent/` + `/health` probe |

---

## Parameters collected (full reference)

These are the values that end up in `agent/.env` and drive generated
observability config. Understanding **why** each exists helps you decide what to
override.

### A. Data-plane endpoints (agent → observability tools)

The agent **pulls** metrics and logs from these URLs. Addresses are chosen from
the **reachability matrix** (Docker DNS vs host port vs remote) so they work
from the agent’s runtime placement—not necessarily the same URL you use in a
browser.

| Parameter | Env var | Required? | Why it is needed |
|---|---|---|---|
| Prometheus URL | `AGENT_PROMETHEUS_URL` | **Required** | Metrics are the primary signal for every diagnosis. Install **fails** without a reachable Prometheus (or an explicit override). |
| Loki URL | `AGENT_LOKI_URL` | **Required** (unless `--allow-degraded`) | Log evidence for runbook correlation. Missing without `--allow-degraded` → **exit 1**; with the flag → metrics-only. |
| Grafana URL | `AGENT_GRAFANA_URL` | Optional | Base URL for annotation delivery. If missing → annotations off. |
| Alertmanager URL | *(report / apply + webhook wiring)* | **Required** (unless `--allow-degraded`) | Required for the reactive Alertmanager → agent path. Missing without `--allow-degraded` → **exit 1**. |

**Typical values**

| Placement | Example Prometheus URL |
|---|---|
| Agent on same Docker network | `http://prometheus:9090` |
| Agent on host, ports published | `http://127.0.0.1:9090` |
| Remote stack | `http://ops.example.com:9090` |

### B. Control-plane webhook (Alertmanager → agent)

| Parameter | Flag / field | Required? | Why |
|---|---|---|---|
| Webhook URL | `--webhook-url` | **Required** when Alertmanager is present (default path) | Alertmanager must POST firing alerts to the agent. Wrong address = silent “agent never runs”. |

How the installer picks a default:

| Agent placement | Default webhook |
|---|---|
| Same Docker network as the stack | `http://diagnostic-agent:8000/alert` |
| Standalone on the local host | `http://host.docker.internal:8001/alert` |
| Remote target | `http://<target-host>:8001/alert` (confirm routability from AM) |

Host port **8001** maps to container port **8000** in the generated compose file
(`agent_host_port`). Override with `--webhook-url` when your network topology
differs (e.g. Kubernetes service DNS, reverse proxy).

### C. Metrics / logs preset

| Parameter | Flag | Required? | Why |
|---|---|---|---|
| Preset | `--preset` | **Required** (default `auto`) | Selects PromQL templates and log label conventions (`generic-prometheus` vs Spring Micrometer `http_server_requests_*`). Wrong preset → empty/wrong metric queries. |

| Value | Use when |
|---|---|
| `auto` | Let the installer infer from container name hints (`platform-service`, `api-gateway`, `spring`, …) → else `generic-prometheus` |
| `generic-prometheus` | Classic `http_requests_total` / community exporters |
| `spring-micrometer` | Spring Boot Actuator / Micrometer naming |

Preset also seeds `redaction.yaml` via `extends:` — redaction is a **hard gate**;
the agent refuses to start with zero rules.

### D. LLM and embeddings

Diagnosis is LLM-backed. You need a **chat** provider and (for RAG runbooks) an
**embeddings** provider.

| Parameter | Env / flag | Required? | Why |
|---|---|---|---|
| Chat provider | `--chat-provider` / `AGENT_CHAT_PROVIDER` | **Required** (auto-selected) | Runs the diagnostic graph |
| Chat model | `--chat-model` / `AGENT_CHAT_MODEL` | Recommended | Model id for that provider |
| Embed provider / model | `AGENT_EMBED_*` (auto with chat) | Recommended when RAG on | Indexes / retrieves runbooks |
| Provider kwargs | `AGENT_CHAT_MODEL_KWARGS` JSON | Often required | e.g. `{"base_url":"http://ollama:11434"}` or `{"region_name":"us-east-1"}` |
| `OPENAI_API_KEY` | env | If provider=`openai` | SDK credential |
| `ANTHROPIC_API_KEY` | env | If provider=`anthropic` | SDK credential |
| `GOOGLE_API_KEY` | env | If provider=`google_genai` | SDK credential |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` | env | If Bedrock | Prefer agent-scoped keys in production |

**Auto-selection order**

1. Reachable **Ollama** container/port → `ollama` + detected base URL  
2. AWS credentials present → `bedrock_converse` + Titan embeddings  
3. `OPENAI_API_KEY` → OpenAI  
4. `ANTHROPIC_API_KEY` → Anthropic  
5. `GOOGLE_API_KEY` → Google GenAI  
6. Interactive: prompt for provider  
7. Non-interactive: fall back to `ollama` with a warning  

### E. Grafana annotations

| Parameter | Env | Required? | Why |
|---|---|---|---|
| Grafana token | `AGENT_GRAFANA_TOKEN` | Optional | Service-account token so the agent can POST org annotations when a diagnosis completes |
| Annotations enabled | `AGENT_GRAFANA_ANNOTATIONS_ENABLED` | Derived | Forced `false` if URL or token is missing |

On Grafana OSS, org-level annotation write typically needs the **Editor** basic
role on the service account. Skip the token during install and add it later via
`observability/grafana/README.md` in the bundle—the agent still runs and writes
JSON audit reports.

### F. Diagnostic email (SMTP)

Separate from Alertmanager’s own email notifier. This is the agent’s
**hypothesis report** mail.

| Parameter | Env | Required? | Why |
|---|---|---|---|
| Email enabled | `AGENT_EMAIL_ENABLED` | Optional (default off unless Mailpit found) | Turn on report delivery |
| SMTP host / port | `AGENT_SMTP_HOST` / `AGENT_SMTP_PORT` | If email on | Relay for outbound mail |
| From / To | `AGENT_SMTP_FROM` / `AGENT_EMAIL_TO` | If email on | Envelope addresses |
| Attach audit JSON | `AGENT_EMAIL_ATTACH_AUDIT` | Optional (default **on**) | Redacted `llm_raw` + prompts as `.json` attachment; set `false` for body-only |
| Attach size cap | `AGENT_EMAIL_ATTACH_AUDIT_MAX_BYTES` | Optional (`262144`) | Skip attachment if redacted JSON exceeds this; email still sends |
| Username / password / STARTTLS | `AGENT_SMTP_*` | If relay requires auth | Credentials |

**Auto:** if **Mailpit** is discovered (HTTP UI on `:8025` and/or Docker
container) → enable SMTP to Mailpit (`container-name:1025`, no auth / no
STARTTLS). Interactive installs without Mailpit still default the SMTP fields
to a Mailpit-style client (`host.docker.internal:1025`). Non-interactive
without Mailpit → email stays disabled.

### G. Safety and packaging (always set by the installer)

| Parameter | Value | Required? | Why |
|---|---|---|---|
| `AGENT_REQUIRE_REDACTION` | `true` | **Required** | Refuses to start with zero redaction rules (tenant/PII safety) |
| `AGENT_RAG_ENABLED` | `true` | Recommended | Runbook retrieval |
| `AGENT_DEFAULT_PRESET` | chosen preset | **Required** | Matches workspace `extends:` |
| `DIAGNOSTIC_AGENT_IMAGE` | `ghcr.io/mskrado/diagnostic-agent:…` | Recommended | Image pin for compose |
| Redaction profile | `workspace/redaction.yaml` → `extends: <preset>` | **Required** | Seeds base secret scrubbing; add tenant rules later |
| `service_map.yaml` | starter topology | Recommended | Blast-radius / dependency context; **edit to match your stack** |

---

## What gets generated

The install output is **self-sufficient**. You do **not** need a separate host
monorepo path for validate / lint / eval / run — everything lives under
`--output`.

```text
<output>/
├── agent/
│   ├── Dockerfile              # thin wrapper FROM the published image
│   ├── docker-compose.yml      # agent service + optional external network
│   ├── .env                    # ALL AGENT_* (+ SDK keys) — do not commit
│   ├── .gitignore              # ignores .env
│   └── workspace/              # complete host workspace
│       ├── agent.yaml          # includes blind_eval: ./blind_eval.yaml
│       ├── metrics/logs/prompt/redaction profiles
│       ├── service_map.yaml    # spring-micrometer: full modular-monolith example
│       ├── scenarios.yaml
│       ├── blind_eval.yaml     # seeded from eval/blind_eval_dataset.yaml
│       └── runbooks/           # full runbook-*.md corpus (RAG)
├── observability/
│   ├── prometheus/alert-rules.generated.yml   # merge into rule_files
│   ├── alertmanager/route.generated.yml       # additive webhook receiver
│   ├── promtail/promtail.generated.yaml       # ensure service= labels
│   └── grafana/README.md                      # token provisioning steps
├── install-report.json         # discovery + decisions (secrets redacted)
└── APPLY.md                    # ordered apply + eval instructions
```

### Bundle file guide

Workspace YAMLs are documented in depth in [WORKSPACE.md](WORKSPACE.md)
(purpose, runtime use, and how to configure each file). Headers inside the
generated files repeat the same instructions.

| Path | Role | What you do after install |
|---|---|---|
| `agent/.env` | Runtime settings: Prometheus/Loki/Grafana URLs, LLM provider + models, SMTP, redaction/RAG flags, image pin. Loaded by Compose via `env_file`. | Fill secrets (API keys, `AGENT_GRAFANA_TOKEN`, AWS keys if Bedrock). Keep `AGENT_DEFAULT_PRESET` aligned with `workspace/agent.yaml` `extends`. **Do not commit.** |
| `agent/docker-compose.yml` | Runs the published image, mounts `./workspace` → `/workspace:ro`, joins the discovered Docker network when present. | `docker compose --env-file .env up -d`. Adjust published port or image pin if needed. |
| `agent/Dockerfile` | Optional thin `FROM` wrapper around the GHCR image. | Prefer pulling the image via Compose; build only if your registry policy requires it. |
| `agent/workspace/*` | Integration profile + runbooks the agent reads on every diagnosis. | Edit `service_map.yaml` and profile overlays to match your stack; see WORKSPACE.md. |
| `observability/prometheus/alert-rules.generated.yml` | Alert rule group intersecting the shipped runbook catalog. | **Merge** into Prometheus `rule_files` (not a full replacement), then reload. |
| `observability/alertmanager/route.generated.yml` | Additive route/receiver → agent webhook. | Merge into Alertmanager config and reload. |
| `observability/promtail/promtail.generated.yaml` | Snippet reminding you to emit `service=` labels. | Align scrapes with `service_map.yaml` names. |
| `observability/grafana/README.md` | How to mint a service-account token for annotations. | Optional; skip if annotations stay off. |
| `install-report.json` | Discovery inventory, decisions, warnings (secrets redacted). | Review placement/URLs before applying. |
| `APPLY.md` | Ordered apply checklist + **Testing** section with **bash and PowerShell** examples (health, `POST /alert`, offline/live blind eval with `--limit` / `--only` / `--judge`). | Follow top to bottom; run the Testing commands before declaring the install done. |

Alert rules are **only** the alerts that intersect the shipped runbook corpus
(so the agent can actually diagnose them). They are not a full replacement for
your existing Prometheus rules—merge the `diagnostic-agent.generated` group.
| Preset | Workspace profile seeding |
|---|---|
| `generic-prometheus` | Thin `extends:` stubs + starter 3-tier `service_map.yaml` |
| `spring-micrometer` | Copied from `examples/spring-modular-monolith/` (service map, logs filters, tenant redaction, prompt) |

Alert rules are **only** the alerts that intersect the shipped runbook catalog
(so the agent can diagnose them). They are not a full replacement for your
existing Prometheus rules—merge the `diagnostic-agent.generated` group.

---

## After install

1. **Review** `install-report.json` (placement, URLs, warnings) and edit
   `agent/workspace/service_map.yaml` only if your service names differ.
2. **Follow** `APPLY.md` (includes a **Testing** section with health, `POST /alert`,
   and offline/live blind-eval commands pinned to this install's host port):
   - Merge Prometheus rules → `POST /-/reload` (needs `--web.enable-lifecycle`)
   - Merge Alertmanager route/receiver → reload
   - Align Promtail/Loki `service=` labels with the service map
   - Mint Grafana token if you want annotations
3. **Start** the agent:
   ```bash
   cd deploy/agent && docker compose --env-file .env up -d
   curl -sf http://127.0.0.1:8001/health
   ```
   Or re-run with `--start`.
4. **Validate / lint / eval** — copy-paste from `APPLY.md` § Testing, or:

   ```bash
   docker run --rm \
     -v "$PWD/deploy/agent/workspace:/workspace:ro" \
     ghcr.io/mskrado/diagnostic-agent:latest \
     sh -c "diag validate && diag lint"

   # Blind eval: -w is on `eval`, before `blind`
   # (install package or pip install -e . / python -m app.cli …)
   diag eval -w ./deploy/agent/workspace blind --limit 3
   diag eval -w ./deploy/agent/workspace blind \
     --live-url http://127.0.0.1:8001 \
     --loki-url http://127.0.0.1:3100 \
     --limit 3
   diag eval -w ./deploy/agent/workspace blind \
     --live-url http://127.0.0.1:8001 \
     --loki-url http://127.0.0.1:3100 \
     --judge
   ```

### Idempotent re-runs

Re-running `diag install --output ./deploy` is safe:

- Identical content → no rewrite  
- Differing content → timestamped `*.bak.<utc>` backup, then replace  
- Use `--dry-run` first in production change windows  

---

## Graceful degradation

Soft-degrade is **opt-in** via `--allow-degraded`. Without that flag, missing
Loki, Alertmanager, or LLM config fails the install instead of writing a
partial bundle.

| Missing tool | Default | With `--allow-degraded` |
|---|---|---|
| **Prometheus** | **Hard fail** — install aborts | **Hard fail** |
| Loki / Promtail | **Hard fail** | Metrics-only diagnosis; warning in report |
| Alertmanager | **Hard fail** | No `route.generated.yml` webhook; manual `POST /alert` still works |
| Grafana | Annotations disabled; audit JSON / email still available | Same |
| Mailpit / SMTP | Email disabled | Same |
| LLM (non-interactive) | **Hard fail** | Blind Ollama default + warning |
| Docker CLI | HTTP/port discovery only (no container DNS names) | Same |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Prometheus is required but was not reachable` | Prom down or wrong host | Start Prometheus or pass `--prometheus-url` |
| Agent healthy but never fires | Webhook URL not reachable from AM | Check placement table; set `--webhook-url`; open AM silences/logs |
| Empty metrics in reports | Wrong `--preset` or `service=` labels | Align preset + service map + PromQL labels |
| `0 redaction rules` / validate fail | Broken `extends:` chain | Keep `redaction.yaml` with `extends: <preset>` |
| `--start` health fail | Port conflict or image pull | Check `8001`, `docker compose logs`, image pin |
| SSH discovery empty | BatchMode / keys | Ensure `ssh -o BatchMode=yes user@host docker ps` works |
| Windows console Unicode errors | Old installer build | Use current release (ASCII status markers) |

---

## Requirements

- Python **3.11+** and the `diagnostic-agent` package (`pip install -e ".[dev]"` or from PyPI when published)
- **Optional but recommended:** Docker CLI (introspection + `--start`), `ssh` (remote `--ssh`), `promtool` (extra rule lint on verify)

Thin wrappers (same args as `diag install`):

```bash
./scripts/diag-install.sh --output ./deploy
pwsh ./scripts/diag-install.ps1 --output ./deploy
```

---

## Quick recipe card

```bash
# 1) Preview against the local host
diag install --output ./deploy --dry-run

# 2) Interactive install
diag install --output ./deploy

# 3) Non-interactive / CI
diag install --output ./deploy --non-interactive --yes \
  --prometheus-url http://prometheus:9090 \
  --loki-url http://loki:3100 \
  --preset generic-prometheus \
  --chat-provider ollama

# 4) Generate, reload stack, start agent
diag install --output ./deploy --non-interactive --yes --apply --start
```
