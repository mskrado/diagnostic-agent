# Installing diagnostic-agent

**Default path: client fork.** Hold a private copy of this repository and run
`diag init` to scaffold your deployment under `client/`. That is the supported
production layout (start scripts, upgrades, ownership boundary). Full lifecycle:
**[CLIENT_FORK.md](CLIENT_FORK.md)**.

```bash
# From a clone of your private fork (or upstream, then mirror — see CLIENT_FORK.md)
./scripts/bootstrap-venv.sh && source .venv/bin/activate   # or one-shot Docker below
diag init
cp client/agent/.env.example client/agent/.env   # fill secrets
./client/scripts/start.sh
```

`diag init` and `diag install` share the same discovery and parameter collection.
This guide documents those shared mechanics (modes, flags, parameters, remote
deploy, Docker vs standalone runtime). Use **`diag init`** unless you explicitly
want a **throwaway** bundle under `deploy/` (gitignored) via `diag install`.

Dependency files and host bootstrap: [DEPENDENCIES.md](DEPENDENCIES.md).

Related docs: [CLIENT_FORK.md](CLIENT_FORK.md) · [INTEGRATING.md](INTEGRATING.md) ·
[WORKSPACE.md](WORKSPACE.md) · [TESTING.md](TESTING.md)

---

## Topics

1. [Default: client fork (`diag init`)](#default-client-fork-diag-init)
2. [Throwaway bundles (`diag install`)](#throwaway-bundles-diag-install)
3. [Obtaining the `diag` CLI](#obtaining-the-diag-cli)
4. [Modes at a glance](#modes-at-a-glance)
5. [Interactive mode](#interactive-mode)
6. [Non-interactive mode](#non-interactive-mode)
7. [CLI flags reference](#cli-flags-reference)
8. [Parameters collected (full reference)](#parameters-collected-full-reference)
9. [What gets generated](#what-gets-generated)
10. [After install](#after-install)
11. [Deploy the install bundle to a remote host](#deploy-the-install-bundle-to-a-remote-host)
12. [Run the agent (Docker image or standalone process)](#run-the-agent-docker-image-or-standalone-process)
13. [Graceful degradation](#graceful-degradation)
14. [Troubleshooting](#troubleshooting)
15. [Requirements](#requirements)
16. [Quick recipe card](#quick-recipe-card)

---

## Default: client fork (`diag init`)

| | |
|---|---|
| **Command** | `diag init` |
| **Output** | `client/` (workspace, compose, scripts, observability snippets, `.upstream-version`) |
| **When** | Every normal deployment — private fork, upgrades via `diag upgrade` |
| **Guide** | [CLIENT_FORK.md](CLIENT_FORK.md) |

```bash
diag init
# common non-interactive:
diag init --accept-defaults --allow-degraded
diag init --pull-image --agent-image ghcr.io/mskrado/diagnostic-agent:1.1.4
```

Then: review `client/workspace/service_map.yaml`, copy `.env`, start with
`./client/scripts/start.sh` (or the generated systemd unit for host `diag serve`).

You do **not** also run `diag install` for the fork model.

---

## Throwaway bundles (`diag install`)

| | |
|---|---|
| **Command** | `diag install --output ./deploy` |
| **Output** | Directory you choose (convention: `deploy/`, gitignored) |
| **When** | CI experiments, one-off bundles, copy-to-remote without a fork |
| **Not for** | Long-lived client ownership under `client/` |

```bash
diag install --output ./deploy
# then follow ./deploy/APPLY.md
```

The rest of this document’s flag and parameter reference applies to **both**
commands (shared collector). Examples often show `diag install --output ./deploy`;
for production, substitute `diag init` and paths under `client/`
(`client/workspace/…`, `client/agent/…`, `./client/scripts/start.sh`).

---

## Obtaining the `diag` CLI

`diag init` / `diag install` are generators: they need a working Python ≥3.11
**once** to write files. How you obtain the CLI is independent of how you later
**run** the agent (Compose, `docker run`, or host `diag serve`).

### Host Python (when the machine already has ≥3.11)

```bash
./scripts/install-system-deps.sh   # optional OS packages; see DEPENDENCIES.md
./scripts/bootstrap-venv.sh        # .venv from requirements.lock
source .venv/bin/activate
diag init                          # default
# diag install --output ./deploy   # throwaway only
```

### One-shot Docker (no usable host Python — typical Amazon Linux 2)

Run the generator inside a short-lived container; the bind mount writes
`client/` (or a bundle) onto the host. Nothing has to keep running in Docker
afterward unless you choose a Docker **runtime**.

**Default — client fork:**

```bash
docker run --rm \
  -v "$PWD:/work" -w /work \
  --network host \
  python:3.12-slim \
  bash -c 'pip install -q -e . && diag init --accept-defaults --allow-degraded'
```

**Throwaway bundle:**

```bash
docker run --rm \
  -v "$PWD:/work" -w /work \
  --network host \
  python:3.12-slim \
  bash -c 'pip install -q -e . && diag install --output ./deploy --accept-defaults --allow-degraded'
```

`--network host` lets discovery reach Prometheus/Loki on the host’s published
ports. Drop it if you only pass URLs via flags and do not need local probes.

Same recipes (upgrades, AL2 notes): [CLIENT_FORK.md §2](CLIENT_FORK.md#2-initialize-your-deployment).

---

## Modes at a glance

| Mode | When to use | Fork (default) | Throwaway bundle |
|---|---|---|---|
| **Interactive** | First-time; confirm every parameter after discovery | `diag init` | `diag install --output ./deploy` |
| **Accept defaults** | Trusted stack; no prompts | `diag init --accept-defaults` | `diag install --output ./deploy --accept-defaults` |
| **Non-interactive** | CI/CD, unattended | `diag init --non-interactive --yes …` | `diag install --output ./deploy --non-interactive --yes …` |
| **Dry-run** | Preview without writing | add `--dry-run` | add `--dry-run` |

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
diag init                              # default (client/)
# diag install --output ./deploy       # throwaway bundle only
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

**Default (client fork):**

```bash
diag init \
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

**Throwaway bundle:**

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

diag init --non-interactive --yes --dry-run   # preview (fork)
diag init --non-interactive --yes             # write client/

# Throwaway alternative:
# diag install --output ./deploy --non-interactive --yes --dry-run
# diag install --output ./deploy --non-interactive --yes
# diag install --output ./deploy --non-interactive --yes --apply --start
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

**Client fork (`diag init`)** — default production layout under `client/`:

```text
client/
├── workspace/                  # profiles, service_map, runbooks, scenarios
├── agent/                      # Compose, Dockerfile, .env / .env.example
├── observability/              # snippets to merge into live stack
├── scripts/                    # start.sh, stop.sh, status.sh, …
├── systemd/                    # diagnostic-agent.service (host serve)
├── docs/OPERATIONS.md
├── .upstream-version
├── install-report.json
└── APPLY.md
```

Full table: [CLIENT_FORK.md §2](CLIENT_FORK.md#2-initialize-your-deployment).

**Throwaway (`diag install --output …`)** — self-sufficient directory (no fork
scripts). Everything for validate / lint / eval / run lives under `--output`:

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

Paths in the table below are relative to the install root (`client/` or
`--output`). For the fork, workspace files live at `workspace/*` (not under
`agent/`); for throwaway bundles they live at `agent/workspace/*`.

### Bundle file guide

Workspace YAMLs are documented in depth in [WORKSPACE.md](WORKSPACE.md)
(purpose, runtime use, and how to configure each file). Headers inside the
generated files repeat the same instructions.

| Path | Role | What you do after install |
|---|---|---|
| `agent/.env` | Runtime settings: Prometheus/Loki/Grafana URLs, LLM provider + models, SMTP, redaction/RAG flags, image pin. Loaded by Compose via `env_file`. | Fill secrets (API keys, `AGENT_GRAFANA_TOKEN`, AWS keys if Bedrock). Keep `AGENT_DEFAULT_PRESET` aligned with workspace `agent.yaml` `extends`. **Do not commit.** |
| `agent/docker-compose.yml` | Runs the image, mounts workspace → `/workspace:ro`, joins the discovered Docker network when present. | `docker compose --env-file .env up -d` (or `./client/scripts/start.sh`). Adjust published port or image pin if needed. |
| `agent/Dockerfile` | Optional thin `FROM` wrapper / self-build context. | Prefer Compose start; build when using fork self-build (default for `diag init`). |
| `workspace/*` (fork) / `agent/workspace/*` (bundle) | Integration profile + runbooks the agent reads on every diagnosis. | Edit `service_map.yaml` and profile overlays to match your stack; see WORKSPACE.md. |
| `observability/prometheus/alert-rules.generated.yml` | Alert rule group intersecting the shipped runbook catalog. | **Merge** into Prometheus `rule_files` (not a full replacement), then reload. |
| `observability/alertmanager/route.generated.yml` | Additive route/receiver → agent webhook. | Merge into Alertmanager config and reload. |
| `observability/promtail/promtail.generated.yaml` | Snippet reminding you to emit `service=` labels. | Align scrapes with `service_map.yaml` names. |
| `observability/grafana/README.md` | How to mint a service-account token for annotations. | Optional; skip if annotations stay off. |
| `install-report.json` | Discovery inventory, decisions, warnings (secrets redacted). | Review placement/URLs before applying. |
| `APPLY.md` | Ordered apply checklist + **Testing** section with **bash and PowerShell** examples (health, `POST /alert`, offline/live blind eval with `--limit` / `--only` / `--judge`). | Follow top to bottom; run the Testing commands before declaring the install done. |

| Preset | Workspace profile seeding |
|---|---|
| `generic-prometheus` | Thin `extends:` stubs + starter 3-tier `service_map.yaml` |
| `spring-micrometer` | Copied from `examples/spring-modular-monolith/` (service map, logs filters, tenant redaction, prompt) |

Alert rules are **only** the alerts that intersect the shipped runbook catalog
(so the agent can diagnose them). They are not a full replacement for your
existing Prometheus rules—merge the `diagnostic-agent.generated` group.

---

## After install

### Client fork (default)

1. Review `client/workspace/service_map.yaml` and `client/APPLY.md`
   / `client/docs/OPERATIONS.md`.
2. `cp client/agent/.env.example client/agent/.env` and fill secrets.
3. Start: `./client/scripts/start.sh` (or `diag serve` via the generated systemd unit).
4. Merge `client/observability/` into your live Prometheus/Alertmanager stack.
5. Upgrades: `diag upgrade` — see [CLIENT_FORK.md](CLIENT_FORK.md).

### Throwaway bundle (`deploy/`)

1. **Review** `install-report.json` (placement, URLs, warnings) and edit
   `agent/workspace/service_map.yaml` only if your service names differ.
2. **Follow** `APPLY.md` (includes a **Testing** section with health, `POST /alert`,
   and offline/live blind-eval commands pinned to this install's host port):
   - Merge Prometheus rules → `POST /-/reload` (needs `--web.enable-lifecycle`)
   - Merge Alertmanager route/receiver → reload
   - Align Promtail/Loki `service=` labels with the service map
   - Mint Grafana token if you want annotations
3. **Start** the agent (same host as the bundle):
   ```bash
   cd deploy/agent && docker compose --env-file .env up -d
   curl -sf http://127.0.0.1:8001/health
   ```
   Or re-run with `--start`. For a **remote** runtime host, copy the bundle
   first ([Deploy the install bundle to a remote host](#deploy-the-install-bundle-to-a-remote-host)),
   then start via Compose, `docker run`, or standalone `diag serve`
   ([Run the agent](#run-the-agent-docker-image-or-standalone-process)).
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

Re-running `diag init` or `diag install --output ./deploy` is safe:

- Identical content → no rewrite  
- Differing content → timestamped `*.bak.<utc>` backup, then replace  
- Use `--dry-run` first in production change windows  

`diag install` is a **generator**. It does not have to run on the machine that
will host the agent. Discovery can target a remote stack (`--target` /
`--ssh`); the written bundle is then copied to the runtime host (next section)
or kept local for Compose on the same box.

---

## Deploy the install bundle to a remote host

Use this when you generated the bundle on a laptop/CI and the agent should run
on another machine (ops VM, EC2, bastion next to Prom/Loki).

### What to copy

| Path under `--output` | Copy? | Notes |
|---|---|---|
| `agent/workspace/` | **Yes** | Profiles, runbooks, `agent.yaml` — bind-mounted at `/workspace` |
| `agent/docker-compose.yml` | **Yes** (Docker runtime) | Or recreate an equivalent Compose / `docker run` |
| `agent/.env` | **Yes, carefully** | Contains secrets. Prefer copying a template and filling secrets on the host; never commit |
| `agent/Dockerfile` | Optional | Thin `FROM` wrapper; Compose can pull GHCR directly |
| `observability/*.generated.yml` | **Merge on the stack host** | Into live Prometheus / Alertmanager / Promtail — not a full replace |
| `install-report.json` / `APPLY.md` | Optional | Operator reference |

**Do not** assume the install output path on your laptop is what production
mounts. Larger hosts often promote the workspace into their own repo (for
example `infrastructure/diagnostic-agent/`) and sync that tree with a host
deploy script. The runtime contract is the same: a directory with `agent.yaml`
(+ profile / runbooks) mounted at `AGENT_WORKSPACE`.

### Layout on the remote host

Pick a stable root, for example `/opt/diagnostic-agent/`:

```text
/opt/diagnostic-agent/
├── docker-compose.yml      # from agent/docker-compose.yml (paths adjusted)
├── .env                    # secrets + AGENT_* (mode 600)
└── workspace/              # from agent/workspace/
    ├── agent.yaml
    ├── …profiles…
    └── runbooks/
```

If Compose stays in `agent/` as generated, keep `workspace/` as a sibling of
`docker-compose.yml` so the relative mount `./workspace:/workspace:ro` still
works.

### Copy with scp / rsync

**bash** (from the machine that has the install output):

```bash
REMOTE=ec2-user@ops.example.com
KEY=$HOME/.ssh/ops.pem
OUT=./deploy          # your --output directory
DEST=/opt/diagnostic-agent

ssh -i "$KEY" "$REMOTE" "sudo mkdir -p $DEST && sudo chown \$USER:\$USER $DEST"

# Workspace (always)
rsync -az -e "ssh -i $KEY" \
  "$OUT/agent/workspace/" "$REMOTE:$DEST/workspace/"

# Compose + env (Docker runtime)
scp -i "$KEY" \
  "$OUT/agent/docker-compose.yml" \
  "$OUT/agent/.env" \
  "$REMOTE:$DEST/"

# Agent process runs as UID 10001 in the published image — host files must be readable
ssh -i "$KEY" "$REMOTE" "chmod -R a+rX $DEST/workspace && chmod 600 $DEST/.env"
```

**PowerShell** (OpenSSH `scp` / `ssh`):

```powershell
$Remote = "ec2-user@ops.example.com"
$Key    = "$HOME\.ssh\ops.pem"
$Out    = ".\deploy"
$Dest   = "/opt/diagnostic-agent"

ssh -i $Key $Remote "sudo mkdir -p $Dest && sudo chown `$USER:`$USER $Dest"
scp -i $Key -r "$Out\agent\workspace" "${Remote}:${Dest}/"
scp -i $Key "$Out\agent\docker-compose.yml" "$Out\agent\.env" "${Remote}:${Dest}/"
ssh -i $Key $Remote "chmod -R a+rX $Dest/workspace && chmod 600 $Dest/.env"
```

After copy, fix Compose volume paths if you flattened the layout (generated file
expects `./workspace` next to `docker-compose.yml`).

### Merge observability on the stack host

On the host that runs Prometheus / Alertmanager (often the same machine):

1. Merge `observability/prometheus/alert-rules.generated.yml` into `rule_files`
   (additive group only).
2. Merge `observability/alertmanager/route.generated.yml` receiver + route.
3. Confirm Promtail/Loki streams emit `service=` labels matching `service_map.yaml`.
4. Reload: `curl -X POST http://<prometheus>/-/reload` and the same for
   Alertmanager (lifecycle API must be enabled).

Confirm the **webhook URL** in the AM snippet is reachable **from Alertmanager**,
not from your laptop. Same Docker network → container DNS
(`http://diagnostic-agent:8000/alert`). Cross-host → published IP/DNS and open
port. Override at install time with `--webhook-url` if needed.

### Start or refresh the agent on the remote host

```bash
ssh -i "$KEY" "$REMOTE" "cd $DEST && docker compose --env-file .env pull && docker compose --env-file .env up -d"
ssh -i "$KEY" "$REMOTE" "curl -sf http://127.0.0.1:8001/health"
```

Workspace-only updates (profile / runbooks) need a **restart or recreate** so
startup reloads YAML and rebuilds the RAG index:

```bash
ssh -i "$KEY" "$REMOTE" "cd $DEST && docker compose --env-file .env up -d --force-recreate diagnostic-agent"
```

### Checklist before declaring remote deploy done

- [ ] `/health` → `status=ok`, `redaction_rules > 0`, expected `preset`
- [ ] `AGENT_PROMETHEUS_URL` / `AGENT_LOKI_URL` resolve **from inside the agent
      container** (Docker DNS or host gateway — not your laptop’s `localhost`)
- [ ] Alertmanager can POST the webhook URL
- [ ] Secrets present on the host only (`.env` mode 600; not in git)
- [ ] Smoke / rule-path as in [TESTING.md](TESTING.md) if this is production

---

## Run the agent (Docker image or standalone process)

The published artifact is `ghcr.io/mskrado/diagnostic-agent:<tag>`. The same
process (`diag serve`) runs inside that image or under a local Python install.
Configuration always comes from **environment variables** + a **workspace
directory**.

### A. Docker Compose (recommended — install bundle)

From the install output (local or after remote copy):

```bash
cd deploy/agent   # or /opt/diagnostic-agent after remote flatten
docker compose --env-file .env up -d
curl -sf http://127.0.0.1:8001/health
```

What Compose wires for you:

| Concern | Typical setting |
|---|---|
| Image | `DIAGNOSTIC_AGENT_IMAGE` / `ghcr.io/mskrado/diagnostic-agent:…` |
| Workspace | `./workspace` → `/workspace:ro`, `AGENT_WORKSPACE=/workspace` |
| Profile | `AGENT_PROFILE_DIR` set from workspace (`diag serve` resolves `agent.yaml`) |
| Observability network | External Docker network when discovery found one (container DNS) |
| Persistence | Named volumes for audit JSONL + Chroma RAG |
| Host port | `8001:8000` (or discovered `agent_host_port`) |

Edit **workspace YAML**, not the image, for stack-specific behaviour. Pin the
image tag for reproducible deploys.

### B. Plain `docker run` (no Compose file)

Use when you already have orchestration elsewhere or want a one-shot container:

```bash
docker run -d --name diagnostic-agent --restart unless-stopped \
  -p 8001:8000 \
  --env-file /opt/diagnostic-agent/.env \
  -e AGENT_WORKSPACE=/workspace \
  -v /opt/diagnostic-agent/workspace:/workspace:ro \
  -v diagnostic_agent_audit:/app/audit \
  -v diagnostic_agent_chroma:/app/chroma_db \
  --network <observability-network> \
  ghcr.io/mskrado/diagnostic-agent:<pinned-tag>
```

Notes:

- Join the **same Docker network** as Prometheus/Loki/Alertmanager when URLs use
  container DNS (`http://prometheus:9090`). Otherwise point `.env` at
  host-reachable URLs (`http://host.docker.internal:9090` or the remote host IP)
  and add `--add-host=host.docker.internal:host-gateway` on Linux if needed.
- Image default `CMD` is `diag serve --host 0.0.0.0 --port 8000`.
- Process user is **UID 10001** — workspace must be world-readable (`chmod -R a+rX`).
- Do **not** set `AGENT_PROFILE_DIR=""` in the environment; an empty string
  shadows workspace discovery. Omit it and let `diag serve` fill it from
  `agent.yaml`, or set it explicitly to `/workspace` / `/workspace/profile`.

### C. Standalone process (`pip` + `diag serve`)

Run on the host (or a VM) without Docker when you prefer a systemd unit or an
existing Python runtime:

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
pip install diagnostic-agent       # or: pip install -e ".[dev]" from a checkout

# Point at the workspace (install bundle or host-owned tree)
export AGENT_WORKSPACE=/opt/diagnostic-agent/workspace
# Optional overrides — normally derived from agent.yaml:
# export AGENT_PROFILE_DIR=/opt/diagnostic-agent/workspace/profile
# export AGENT_DEFAULT_PRESET=spring-micrometer

# Load the same knobs Compose would inject (URLs, LLM, SMTP, …)
set -a && source /opt/diagnostic-agent/.env && set +a

diag serve --host 0.0.0.0 --port 8000
# equivalent: python -m app.cli serve --host 0.0.0.0 --port 8000
```

**systemd** sketch:

```ini
[Unit]
Description=diagnostic-agent
After=network-online.target

[Service]
Type=simple
User=diagagent
WorkingDirectory=/opt/diagnostic-agent
EnvironmentFile=/opt/diagnostic-agent/.env
Environment=AGENT_WORKSPACE=/opt/diagnostic-agent/workspace
ExecStart=/opt/diagnostic-agent/.venv/bin/diag serve --host 0.0.0.0 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Standalone specifics:

- Prometheus/Loki URLs must be reachable **from that host process** (often
  `http://127.0.0.1:9090` if ports are published, not Docker DNS names).
- Alertmanager’s webhook must reach this process’s bind address/port (firewall /
  reverse proxy as needed).
- Writable dirs: audit log (`AGENT_AUDIT_LOG_DIR`) and Chroma
  (`AGENT_CHROMA_PATH`) — create them and give the service user write access.
- LLM credentials: same as Docker (`OPENAI_API_KEY`, AWS instance role / keys for
  Bedrock, etc.). Prefer instance roles in production when using Bedrock.

### Configuration surface (both runtimes)

| Area | How to set | Docs |
|---|---|---|
| Workspace / profiles / runbooks | Files under `AGENT_WORKSPACE` | [WORKSPACE.md](WORKSPACE.md) |
| Prom / Loki / Grafana / SMTP / LLM | `AGENT_*` in `.env` or process env | [`.env.example`](../.env.example), [INTEGRATING.md](INTEGRATING.md) |
| Image pin | `DIAGNOSTIC_AGENT_IMAGE` (Compose) | Install bundle `.env` |
| Webhook path | Alertmanager → `http://<agent-host>:8000/alert` (container) or published `:8001` | Install `route.generated.yml` |
| Redaction fail-closed | `AGENT_REQUIRE_REDACTION=true` (default) | Must resolve ≥1 rule or refuse to start |

`GET /health` is the smoke check: `status`, `preset`, `redaction_rules`,
`service_map`, `models`, `rag_available`.

### Choosing a runtime

| Runtime | Prefer when |
|---|---|
| **Compose / `docker run`** | Agent sits next to the observability stack; you want network DNS + volume isolation |
| **Standalone `diag serve`** | No Docker on the agent host, or you already manage Python services with systemd |
| **Host-owned workspace in a monorepo** | Production hosts that version profile/runbooks beside compose (installer output is a seed, then promote) — see [INTEGRATING.md](INTEGRATING.md) |

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
| `0 redaction rules` / validate fail | Broken `extends:` chain or empty mount | Keep `redaction.yaml` with `extends: <preset>`; `chmod a+rX` workspace for UID 10001 |
| `--start` health fail | Port conflict or image pull | Check `8001`, `docker compose logs`, image pin |
| SSH discovery empty | BatchMode / keys | Ensure `ssh -o BatchMode=yes user@host docker ps` works |
| Remote agent can’t reach Prom/Loki | `.env` still has laptop `localhost` or wrong Docker network | Use container DNS on shared network, or host-gateway / published ports from the agent host |
| Standalone `diag serve` ignores workspace | `AGENT_WORKSPACE` unset / wrong cwd | Export `AGENT_WORKSPACE` to the directory that contains `agent.yaml` |
| Windows console Unicode errors | Old installer build | Use current release (ASCII status markers) |

---

## Requirements

- **Either** Python **3.11+** and the `diagnostic-agent` package
  (`./scripts/bootstrap-venv.sh`, `pip install -e ".[dev]"`, or PyPI when published),
  **or** Docker to run the [one-shot generator](#obtaining-the-diag-cli) when the
  host has no usable Python (e.g. Amazon Linux 2)
- **Optional but recommended:** Docker CLI (introspection + `--start` + one-shot
  init), `ssh` (remote `--ssh`), `promtool` (extra rule lint on verify)

Default install command: **`diag init`** → `client/`. Full fork lifecycle:
[CLIENT_FORK.md](CLIENT_FORK.md).

Thin wrappers for the throwaway **`diag install`** path:

```bash
./scripts/diag-install.sh --output ./deploy
pwsh ./scripts/diag-install.ps1 --output ./deploy
```

Full dependency layout: [DEPENDENCIES.md](DEPENDENCIES.md).

---

## Quick recipe card

```bash
# 0) Get `diag` — host venv OR one-shot Docker (see Obtaining the diag CLI)
#    ./scripts/bootstrap-venv.sh && source .venv/bin/activate
#    # or: docker run --rm -v "$PWD:/work" -w /work --network host python:3.12-slim \
#    #        bash -c 'pip install -q -e . && diag init --accept-defaults --allow-degraded'

# === Default: client fork ===
diag init --dry-run
diag init
cp client/agent/.env.example client/agent/.env
./client/scripts/start.sh

# Non-interactive fork scaffold
diag init --accept-defaults --allow-degraded --yes

# === Throwaway bundle only (not the fork model) ===
diag install --output ./deploy --dry-run
diag install --output ./deploy
diag install --output ./deploy-ops \
  --target ops.example.com \
  --ssh ec2-user@ops.example.com
diag install --output ./deploy --non-interactive --yes \
  --prometheus-url http://prometheus:9090 \
  --loki-url http://loki:3100 \
  --preset generic-prometheus \
  --chat-provider ollama
# Copy deploy/ to a remote host if needed — see “Deploy the install bundle…”
```
