# Install and operate diagnostic-agent

This is the single install guide. Follow **Part 1** top to bottom to stand up a
production deployment; **Part 2** is reference material you only need when you
want to override a default.

**The install model is a client fork.** Each deployment holds a private copy of
this repository: upstream ships the product code, you own everything under
`client/`. `diag init` scaffolds that directory, `diag upgrade` merges new
upstream releases, and the ownership split keeps merges conflict-free.

```bash
# In your private copy of this repo, on the deployment host
./scripts/bootstrap-venv.sh && source .venv/bin/activate
diag init
cp client/agent/.env.example client/agent/.env    # fill secrets
./client/scripts/start.sh
```

Related docs: [WORKSPACE.md](WORKSPACE.md) (workspace file reference) ·
[INTEGRATING.md](INTEGRATING.md) (manual wiring without the generator) ·
[SCAN.md](SCAN.md) / [DRAFT.md](DRAFT.md) / [DRIFT.md](DRIFT.md)
(evidence → draft → gate) · [TESTING.md](TESTING.md) (operator smoke tests)

---

## Topics

**Part 1 — Install**

1. [Create a private copy](#1-create-a-private-copy)
2. [Get the `diag` CLI](#2-get-the-diag-cli)
3. [Scaffold your deployment (`diag init`)](#3-scaffold-your-deployment-diag-init)
4. [Start the agent](#4-start-the-agent)
5. [Customize your workspace (scan → draft)](#5-customize-your-workspace)
6. [Wire your observability stack](#6-wire-your-observability-stack)
7. [Verify](#7-verify)
8. [Upgrade](#8-upgrade)
9. [Air-gapped installs and upgrades](#9-air-gapped-installs-and-upgrades)
10. [Commit policy and reproducible builds](#10-commit-policy-and-reproducible-builds)

**Part 2 — Reference**

11. [Ownership contract](#ownership-contract)
12. [Modes at a glance](#modes-at-a-glance)
13. [Interactive mode](#interactive-mode)
14. [Non-interactive mode](#non-interactive-mode)
15. [CLI flags reference](#cli-flags-reference)
16. [Parameters collected (full reference)](#parameters-collected-full-reference)
17. [What gets generated](#what-gets-generated)
18. [Run the agent (Docker image or standalone process)](#run-the-agent-docker-image-or-standalone-process)
19. [Appendix: throwaway bundles (`diag install`)](#appendix-throwaway-bundles-diag-install)
20. [Graceful degradation](#graceful-degradation)
21. [Troubleshooting](#troubleshooting)
22. [Quick recipe card](#quick-recipe-card)

---

# Part 1 — Install

## 1. Create a private copy

GitHub **Fork** inherits public visibility. For a private deployment,
mirror-clone instead:

```bash
git clone --bare https://github.com/mskrado/diagnostic-agent.git
cd diagnostic-agent.git
# Replace YOUR_ORG with your GitHub org/user — do not leave angle brackets in the URL
git push --mirror https://github.com/YOUR_ORG/diagnostic-agent.git
cd ..
git clone https://github.com/YOUR_ORG/diagnostic-agent.git
cd diagnostic-agent
git remote add upstream https://github.com/mskrado/diagnostic-agent.git
```

The `upstream` remote is what `diag upgrade` fetches releases from later.

---

## 2. Get the `diag` CLI

`diag init` is a **generator**: it needs a working Python ≥3.11 **once** to write
files. How you obtain the CLI is independent of how you later **run** the agent
(Compose, `docker run`, or host `diag serve`).

### Option A — host Python (machine already has ≥3.11)

```bash
./scripts/install-system-deps.sh   # yum/dnf/apt packages (AL2, AL2023, Ubuntu)
# Amazon Linux 2 only: follow the script's pyenv + openssl11 instructions first
./scripts/bootstrap-venv.sh        # .venv from requirements.lock + `diag`
./scripts/bootstrap-venv.sh --dev  # ...plus pytest tooling
source .venv/bin/activate
diag init
```

`bootstrap-venv.sh` refuses interpreters older than 3.11 and installs from
`requirements.lock`, so every host gets the same package versions as CI and the
published image.

### Option B — one-shot Docker (no usable host Python, typical Amazon Linux 2)

Run the generator in a short-lived container; the bind mount writes `client/`
onto the host. Nothing keeps running in Docker afterward unless you also choose
a Docker runtime.

```bash
docker run --rm \
  -v "$PWD:/work" -w /work \
  --network host \
  python:3.12-slim \
  bash -c 'pip install -q -e . && diag init --accept-defaults --allow-degraded'
```

`--network host` lets discovery reach Prometheus/Loki on the host's published
ports. Drop it if you pass URLs via flags and do not need local probes.

### Dependency files

Do not maintain package lists by hand — these files are the source of truth:

| File | Role |
|------|------|
| **`requirements.txt`** | Runtime dependency *ranges* — source of truth for packaging and Docker when no lock is used |
| **`requirements.lock`** | Exact pins produced by `pip-compile` from `requirements.txt` — use this for reproducible host/CI/image installs |
| **`requirements-dev.txt`** | Test/dev-only packages (`pytest`, …), exposed as the `dev` extra |
| **`pyproject.toml`** | Package metadata, `requires-python = ">=3.11"`, console scripts; reads the requirements files via setuptools dynamic deps so `pip install .` and `pip install -r` cannot drift |
| **`.python-version`** | Hint for pyenv / asdf (`3.12`) |
| **`deps/*.txt`** | OS packages (yum/apt) needed to *build* Python or compile wheels on the host |

```text
pyproject.toml  ──reads──►  requirements.txt  ──pip-compile──►  requirements.lock
                    └──reads──►  requirements-dev.txt  (optional extra: .[dev])
```

The `Dockerfile` prefers `requirements.lock` when present. Offline packs ship a
wheelhouse built from the same lock.

**Regenerating the lock** after changing `requirements.txt` (CI uses Python 3.12):

```bash
pip install pip-tools
pip-compile requirements.txt -o requirements.lock --strip-extras
```

CI fails if the committed lock no longer matches a seeded recompile of
`requirements.txt` (see `.github/workflows/ci.yml`).

### Requirements summary

- **Either** Python **3.11+** and the `diagnostic-agent` package
  (`./scripts/bootstrap-venv.sh`, `pip install -e ".[dev]"`, or PyPI when
  published), **or** Docker for the one-shot generator above
- **Optional but recommended:** Docker CLI (introspection, `--start`, one-shot
  init), `ssh` (remote `--ssh` discovery), `promtool` (extra rule lint on verify)

---

## 3. Scaffold your deployment (`diag init`)

```bash
diag init
```

This discovers Prometheus/Loki/Grafana/Alertmanager (and optional Ollama),
confirms every parameter with you, and writes:

| Path | Purpose |
|------|---------|
| `client/workspace/` | Profiles, service map, runbooks, scenarios |
| `client/agent/` | Docker Compose, `.env`, build context |
| `client/observability/` | Prometheus/Alertmanager/Promtail snippets to merge |
| `client/scripts/` | `start.sh`, `stop.sh`, `status.sh`, `logs.sh`, `start.ps1` |
| `client/systemd/` | `diagnostic-agent.service` for standalone (non-Docker) runs |
| `client/docs/OPERATIONS.md` | Seeded operational notes |
| `client/.upstream-version` | Upstream release marker |
| `client/.github/workflows/client-validate.yml` | CI for your workspace |

Preview first in a production change window with `diag init --dry-run`.

### Common options

```bash
# Non-interactive against a known stack
diag init --accept-defaults --prometheus-url http://127.0.0.1:9090 ...

# Pull prebuilt GHCR image instead of building from source (online hosts)
diag init --pull-image --agent-image ghcr.io/mskrado/diagnostic-agent:1.1.4

# Internal PyPI mirror for air-gapped builds
diag init --pip-index-url https://pypi.internal/simple/
```

Full flag list: [CLI flags reference](#cli-flags-reference). Every parameter and
why it exists: [Parameters collected](#parameters-collected-full-reference).

### Build from source is the default

`diag init` builds `diagnostic-agent:local` from your fork so air-gapped and
internal-mirror hosts never need GHCR.

The generated build uses the **repo root as its Docker build context**
(`context: ../..`, `dockerfile: client/agent/Dockerfile`), because the image is
built from `app/`, `runbooks/` and `requirements.lock`. **Deploy the whole fork
to the host** — copying only `client/` gives you a compose file that cannot
build. Use `--pull-image` if you would rather ship just the workspace and pull a
prebuilt image.

### Re-running is safe

- Identical content → no rewrite
- Differing content → timestamped `*.bak.<utc>` backup, then replace
- `--dry-run` previews without writing

Settings you keep in `client/agent/.env` survive a re-run; edits to generated
compose files do not.

---

## 4. Start the agent

```bash
cp client/agent/.env.example client/agent/.env   # first time only; fill secrets
./client/scripts/start.sh
# PowerShell: .\client\scripts\start.ps1
```

Standalone (no Docker): copy `client/systemd/diagnostic-agent.service` to
`/etc/systemd/system/`, install `diag` on the host, and enable the unit. Runtime
details for both paths:
[Run the agent](#run-the-agent-docker-image-or-standalone-process).

---

## 5. Customize your workspace

`diag init` seeds a starter workspace from discovery. Prefer filling it from
**live evidence** rather than guessing YAML by hand:

### Scan → draft → review

```bash
# 1) See what Prometheus / Loki / Alertmanager already expose
diag scan -w client/workspace --out ./scan-evidence.json \
  --alertmanager-url http://alertmanager:9093

# 2) Stage a draft next to the workspace (does not overwrite client/workspace)
diag draft -w client/workspace --bundle ./scan-evidence.json --out ./diag-draft
diag validate -w ./diag-draft && diag lint -w ./diag-draft

# 3) Diff, merge what you want into client/workspace/, then delete the staging dir
```

| Step | Command | What it does |
|---|---|---|
| **Scan** | [`diag scan`](SCAN.md) | Read-only report + optional JSON bundle |
| **Draft** | [`diag draft`](DRAFT.md) | Writes only values the stack confirms (optional `--llm` for prompt/runbook drafts) |
| **Drift** | [`diag drift`](DRIFT.md) | Later: fail CI when the workspace no longer matches the stack |

Nothing lands in `client/workspace/` unless you pass `diag draft --in-place`
(and `--force` to clobber). Default is a staging directory you review.

### Still edit by hand

After the draft (or instead of it), touch the files that need human judgment:

1. **`client/workspace/service_map.yaml`** — topology (required for blast radius).
2. **`client/workspace/runbooks/`** — replace reference corpus with your own.
3. **`client/workspace/prompt_profile.yaml`** — platform naming, golden commands
   ([playbook](PROMPT_PROFILE_AUTHORING.md)).
4. **`client/agent/.env`** — LLM provider, URLs, SMTP (never commit).

File-by-file reference: [WORKSPACE.md](WORKSPACE.md). Manual-only path (no
generator): [INTEGRATING.md](INTEGRATING.md).

---

## 6. Wire your observability stack

Merge `client/observability/` into your live configs — these are **additive
snippets**, not replacements:

1. Merge `observability/prometheus/alert-rules.generated.yml` into `rule_files`
   (the `diagnostic-agent.generated` group only).
2. Merge `observability/alertmanager/route.generated.yml` receiver + route.
3. Confirm Promtail/Loki streams emit `service=` labels matching
   `service_map.yaml`.
4. Reload: `curl -X POST http://<prometheus>/-/reload` and the same for
   Alertmanager (the lifecycle API must be enabled).
5. Optional: mint a Grafana service-account token per
   `observability/grafana/README.md` to turn on annotations.

Confirm the **webhook URL** is reachable **from Alertmanager**, not from your
laptop. Same Docker network → container DNS
(`http://diagnostic-agent:8000/alert`). Cross-host → published IP/DNS with the
port open. Override at init time with `--webhook-url`.

Alert rules cover **only** the alerts that intersect the shipped runbook corpus,
so the agent can actually diagnose what it receives.

---

## 7. Verify

```bash
curl -sf http://127.0.0.1:8001/health
```

`GET /health` reports `status`, `preset`, `redaction_rules`, `service_map`,
`models`, and `rag_available`. `status=degraded` or `redaction_rules=0` means
the workspace was not found or is empty.

Configuration and content checks (no LLM credentials or running stack needed):

```bash
docker run --rm -v "$PWD/client/workspace:/workspace:ro" \
  ghcr.io/mskrado/diagnostic-agent:latest \
  sh -c "diag validate && diag lint"
```

Workspace vs stack (needs Prometheus/Loki, or a saved scan bundle):

```bash
# Live
diag drift -w client/workspace

# CI / air-gap — commit or upload a scan bundle, then:
diag drift -w client/workspace --bundle ./scan-evidence.json --no-oracle
```

`diag init` scaffolds an optional drift step in
`client/.github/workflows/client-validate.yml` when the repo variable
`DRIFT_BUNDLE` is set. Details: [DRIFT.md](DRIFT.md).

Blind eval — note `-w` belongs on `eval`, before `blind`:

```bash
diag eval -w ./client/workspace blind --limit 3
diag eval -w ./client/workspace blind \
  --live-url http://127.0.0.1:8001 \
  --loki-url http://127.0.0.1:3100 \
  --limit 3
diag eval -w ./client/workspace blind \
  --live-url http://127.0.0.1:8001 \
  --loki-url http://127.0.0.1:3100 \
  --judge
```

Operator smoke tests, remote rule-path checks, and runbook E2E wrappers:
[TESTING.md](TESTING.md).

---

## 8. Upgrade

Before every upgrade, confirm you have not edited upstream paths:

```bash
diag doctor --check-fork
```

Merge a release:

```bash
git fetch upstream --tags
diag upgrade --target v1.2.0
./client/scripts/start.sh          # rebuild
```

`diag upgrade` refuses to proceed if upstream-owned files were modified locally.
It updates `client/.upstream-version` and prints corpus diffs (`runbooks/`,
presets) so you can port improvements into `client/workspace/runbooks/`
deliberately. Use `--skip-drift-check` only when you have already accepted the
conflicts you are about to get.

The drift check compares your working tree against `HEAD`, so it catches edits
you have not committed. If you deliberately commit a patch to an upstream path,
the check stays quiet and `git merge` reports the conflict instead — expected,
but it is why carrying local patches is discouraged. Upstream fixes belong in a
PR to this repo; everything host-specific belongs under `client/`.

---

## 9. Air-gapped installs and upgrades

On a connected machine, build a pack per release:

```bash
./scripts/build-offline-pack.sh 1.2.0
# dist/offline-pack-1.2.0/{*.bundle,wheelhouse.tar.gz,base-image.tar}
```

Carry the directory to the isolated host, then:

```bash
docker load -i base-image.tar
diag upgrade --from-pack /path/to/offline-pack-1.2.0
./client/scripts/start.sh
```

---

## 10. Commit policy and reproducible builds

Commit to **your** private repo:

- `client/workspace/` (except secrets)
- `client/agent/.env.example`, compose, Dockerfile
- `client/scripts/`, `client/docs/`
- Your own top-level docs

Never commit:

- `client/agent/.env`
- `client/**/install-report.json`

### Reproducible builds

- **`requirements.lock`** — pinned deps; CI fails if it drifts from `requirements.txt`.
- **`BASE_IMAGE`** — the Python base image. Pin it by digest for production.
- **`PIP_INDEX_URL`** / **`PIP_EXTRA_INDEX_URL`** — point builds at an internal PyPI proxy.

The generated compose reads all three from `client/agent/.env`, so set them
there rather than editing the compose file — that survives a re-run of
`diag init`:

```bash
# client/agent/.env
BASE_IMAGE=python:3.12-slim@sha256:<digest>
PIP_INDEX_URL=https://pypi.internal/simple/
```

Empty or unset index URLs mean "use the public PyPI index".

---

# Part 2 — Reference

## Ownership contract

| Owner | Paths |
|-------|-------|
| **Upstream** (this repo) | `app/`, `runbooks/`, `examples/`, `eval/`, `tests/`, `docs/`, `scripts/`, `Dockerfile`, `requirements*.txt`, `pyproject.toml`, `.github/` |
| **Client** (your fork) | `client/**` — workspace, compose, `.env`, scripts, docs |

Upstream ships `client/` **empty** (only `README.md` + `.gitkeep`). Never edit
upstream-owned files in your fork — custom runbooks belong in
`client/workspace/runbooks/`.

---

## Modes at a glance

| Mode | When to use | Command |
|---|---|---|
| **Interactive** | First-time; confirm every parameter after discovery | `diag init` |
| **Accept defaults** | Trusted stack; no prompts | `diag init --accept-defaults` |
| **Non-interactive** | CI/CD, unattended | `diag init --non-interactive --yes …` |
| **Dry-run** | Preview without writing | add `--dry-run` |

If stdin is not a terminal (piped input, CI shell), interactive mode switches
itself to non-interactive and says so, rather than silently accepting defaults.
`Ctrl-C` at any prompt exits cleanly with code `130` and writes nothing.

The same modes, flags, and parameters apply to the throwaway `diag install`
command described in the [appendix](#appendix-throwaway-bundles-diag-install);
both share one discovery and collection implementation.

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
as `***`). They only land in `agent/.env`, which is gitignored.

---

## Interactive mode

Best for humans standing up the agent against a live stack.

```bash
diag init
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

3. **Container-reachability rewrite** — the generator probes from *your* host, so
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
$ diag init

diag init - target=local output=client

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
Write these settings? [Y/n]:

Wrote 21 file(s)
  ...

verify OK

Next steps
----------
  1. Scan     diag scan -w client/workspace --out ./scan-evidence.json
  2. Draft    diag draft -w client/workspace --bundle ./scan-evidence.json
  3. Review   merge ./diag-draft into client/workspace/
  4. Copy     client/agent/.env.example -> client/agent/.env
  5. Start    ./client/scripts/start.sh
  6. Health   curl -sf http://127.0.0.1:8001/health
  7. Wire     merge client/observability into your live stack
```

### Remote discovery

Discovery does not have to run on the machine that will host the agent:

```bash
diag init --target ops.example.com --ssh ec2-user@ops.example.com
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

diag init --non-interactive --yes --dry-run    # preview
diag init --non-interactive --yes              # write client/
```

`--yes` skips the confirmation prompt that `--apply` would otherwise show before
reloading a live Prometheus/Alertmanager.

---

## CLI flags reference

Shared by `diag init` and `diag install`, except where noted.

| Flag | Required? | Default | Purpose |
|---|---|---|---|
| `--output` / `-o` | No for `init`, **yes** for `install` | `client/` (`init`) | Output directory |
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

`diag init` only:

| Flag | Default | Purpose |
|---|---|---|
| `--pull-image` | off | Pull the prebuilt GHCR image instead of building from source |
| `--agent-image` | GHCR `:latest` | Image reference when pulling |
| `--base-image` | `python:3.12-slim` | Python base image for self-build |
| `--pip-index-url` / `--pip-extra-index-url` | — | Internal PyPI mirror for image builds |

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
| Prometheus URL | `AGENT_PROMETHEUS_URL` | **Required** | Metrics are the primary signal for every diagnosis. Init **fails** without a reachable Prometheus (or an explicit override). |
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

How the default is picked:

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
| `auto` | Let discovery infer from container name hints (`platform-service`, `api-gateway`, `spring`, …) → else `generic-prometheus` |
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
role on the service account. Skip the token during init and add it later via
`observability/grafana/README.md`—the agent still runs and writes JSON audit
reports.

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
STARTTLS). Interactive runs without Mailpit still default the SMTP fields
to a Mailpit-style client (`host.docker.internal:1025`). Non-interactive
without Mailpit → email stays disabled.

### G. Safety and packaging (always set)

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

### Client fork (`diag init`)

```text
client/
├── workspace/                  # profiles, service_map, runbooks, scenarios
├── agent/                      # Compose, Dockerfile, .env / .env.example
├── observability/              # snippets to merge into live stack
├── scripts/                    # start.sh, stop.sh, status.sh, logs.sh, start.ps1
├── systemd/                    # diagnostic-agent.service (host serve)
├── docs/OPERATIONS.md
├── .upstream-version
├── .github/workflows/client-validate.yml
├── install-report.json
└── APPLY.md
```

### Throwaway bundle (`diag install --output …`)

Self-sufficient directory with no fork scripts; workspace sits under `agent/`:

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

### File guide

Paths are relative to the install root (`client/` or `--output`). Workspace YAMLs
are documented in depth in [WORKSPACE.md](WORKSPACE.md); headers inside the
generated files repeat the same instructions.

| Path | Role | What you do after install |
|---|---|---|
| `agent/.env` | Runtime settings: Prometheus/Loki/Grafana URLs, LLM provider + models, SMTP, redaction/RAG flags, image pin. Loaded by Compose via `env_file`. | Fill secrets (API keys, `AGENT_GRAFANA_TOKEN`, AWS keys if Bedrock). Keep `AGENT_DEFAULT_PRESET` aligned with workspace `agent.yaml` `extends`. **Do not commit.** |
| `agent/docker-compose.yml` | Runs the image, mounts the workspace → `/workspace:ro`, joins the discovered Docker network when present. | `./client/scripts/start.sh`, or `docker compose --env-file .env up -d`. Adjust published port or image pin if needed. |
| `agent/Dockerfile` | Self-build context (fork default) or thin `FROM` wrapper. | Build from the repo root; use `--pull-image` to skip building. |
| `workspace/*` (fork) / `agent/workspace/*` (bundle) | Integration profile + runbooks the agent reads on every diagnosis. | Edit `service_map.yaml` and profile overlays to match your stack; see WORKSPACE.md. |
| `observability/prometheus/alert-rules.generated.yml` | Alert rule group intersecting the shipped runbook catalog. | **Merge** into Prometheus `rule_files` (not a full replacement), then reload. |
| `observability/alertmanager/route.generated.yml` | Additive route/receiver → agent webhook. | Merge into Alertmanager config and reload. |
| `observability/promtail/promtail.generated.yaml` | Snippet reminding you to emit `service=` labels. | Align scrapes with `service_map.yaml` names. |
| `observability/grafana/README.md` | How to mint a service-account token for annotations. | Optional; skip if annotations stay off. |
| `install-report.json` | Discovery inventory, decisions, warnings (secrets redacted). | Review placement/URLs before applying. Never commit. |
| `APPLY.md` | Ordered apply checklist + **Testing** section with **bash and PowerShell** examples (health, `POST /alert`, offline/live blind eval with `--limit` / `--only` / `--judge`). | Follow top to bottom; run the Testing commands before declaring the install done. |

| Preset | Workspace profile seeding |
|---|---|
| `generic-prometheus` | Thin `extends:` stubs + starter 3-tier `service_map.yaml` |
| `spring-micrometer` | Copied from `examples/spring-modular-monolith/` (service map, logs filters, tenant redaction, prompt) |

---

## Run the agent (Docker image or standalone process)

The published artifact is `ghcr.io/mskrado/diagnostic-agent:<tag>`; forks build
the equivalent `diagnostic-agent:local`. The same process (`diag serve`) runs
inside that image or under a local Python install. Configuration always comes
from **environment variables** + a **workspace directory**.

### A. Docker Compose (recommended)

```bash
./client/scripts/start.sh
# equivalent: cd client/agent && docker compose --env-file .env up -d
curl -sf http://127.0.0.1:8001/health
```

What Compose wires for you:

| Concern | Typical setting |
|---|---|
| Image | `DIAGNOSTIC_AGENT_IMAGE` / `diagnostic-agent:local` |
| Workspace | workspace dir → `/workspace:ro`, `AGENT_WORKSPACE=/workspace` |
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

### C. Standalone process (`diag serve`)

Run on the host without Docker when you prefer a systemd unit or an existing
Python runtime. `diag init` generates
`client/systemd/diagnostic-agent.service` for this path.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
pip install diagnostic-agent       # or: ./scripts/bootstrap-venv.sh from your fork

export AGENT_WORKSPACE=/opt/diagnostic-agent/client/workspace
# Optional overrides — normally derived from agent.yaml:
# export AGENT_PROFILE_DIR=/opt/diagnostic-agent/client/workspace/profile
# export AGENT_DEFAULT_PRESET=spring-micrometer

# Load the same knobs Compose would inject (URLs, LLM, SMTP, …)
set -a && source /opt/diagnostic-agent/client/agent/.env && set +a

diag serve --host 0.0.0.0 --port 8000
# equivalent: python -m app.cli serve --host 0.0.0.0 --port 8000
```

**systemd** sketch (the generated unit is equivalent):

```ini
[Unit]
Description=diagnostic-agent
After=network-online.target

[Service]
Type=simple
User=diagagent
WorkingDirectory=/opt/diagnostic-agent
EnvironmentFile=/opt/diagnostic-agent/client/agent/.env
Environment=AGENT_WORKSPACE=/opt/diagnostic-agent/client/workspace
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
| Prom / Loki / Grafana / SMTP / LLM | `AGENT_*` in `.env` or process env | [`.env.example`](../.env.example), [Parameters collected](#parameters-collected-full-reference) |
| Image pin | `DIAGNOSTIC_AGENT_IMAGE` (Compose) | `client/agent/.env` |
| Webhook path | Alertmanager → `http://<agent-host>:8000/alert` (container) or published `:8001` | Generated `route.generated.yml` |
| Redaction fail-closed | `AGENT_REQUIRE_REDACTION=true` (default) | Must resolve ≥1 rule or refuse to start |

### Choosing a runtime

| Runtime | Prefer when |
|---|---|
| **Compose / `docker run`** | Agent sits next to the observability stack; you want network DNS + volume isolation |
| **Standalone `diag serve`** | No Docker on the agent host, or you already manage Python services with systemd |
| **Host-owned workspace in a monorepo** | Production hosts that version profile/runbooks beside compose — see [INTEGRATING.md](INTEGRATING.md) |

---

## Appendix: throwaway bundles (`diag install`)

`diag install` writes the same discovery output into a directory you choose,
**without** the fork lifecycle: no start scripts, no systemd unit, no
`diag upgrade`. Use it for CI experiments, one-off bundles, and copy-to-remote
without a private repo copy. For anything long-lived, prefer `diag init`.

```bash
diag install --output ./deploy
# then follow ./deploy/APPLY.md
```

Flags, parameters, modes, and degradation rules are identical to `diag init`;
only the output layout differs
([What gets generated](#what-gets-generated)). `--output` is required.

Thin wrappers:

```bash
./scripts/diag-install.sh --output ./deploy
pwsh ./scripts/diag-install.ps1 --output ./deploy
```

### After a bundle install

1. **Review** `install-report.json` (placement, URLs, warnings) and edit
   `agent/workspace/service_map.yaml` if your service names differ.
2. **Follow** `APPLY.md` — it includes a Testing section pinned to this install's
   host port.
3. **Start** the agent:

   ```bash
   cd deploy/agent && docker compose --env-file .env up -d
   curl -sf http://127.0.0.1:8001/health
   ```

   Or re-run with `--start`.
4. **Validate / lint / eval** using the [Verify](#7-verify) commands with
   `./deploy/agent/workspace` as the workspace path.

### Deploy a bundle to a remote host

Use this when you generated the bundle on a laptop or in CI and the agent should
run on another machine (ops VM, EC2, bastion next to Prom/Loki).

**What to copy**

| Path under `--output` | Copy? | Notes |
|---|---|---|
| `agent/workspace/` | **Yes** | Profiles, runbooks, `agent.yaml` — bind-mounted at `/workspace` |
| `agent/docker-compose.yml` | **Yes** (Docker runtime) | Or recreate an equivalent Compose / `docker run` |
| `agent/.env` | **Yes, carefully** | Contains secrets. Prefer copying a template and filling secrets on the host; never commit |
| `agent/Dockerfile` | Optional | Thin `FROM` wrapper; Compose can pull GHCR directly |
| `observability/*.generated.yml` | **Merge on the stack host** | Into live Prometheus / Alertmanager / Promtail — not a full replace |
| `install-report.json` / `APPLY.md` | Optional | Operator reference |

**Do not** assume the output path on your laptop is what production mounts.
Larger hosts often promote the workspace into their own repo (for example
`infrastructure/diagnostic-agent/`) and sync that tree with a deploy script. The
runtime contract is the same: a directory with `agent.yaml` (+ profile /
runbooks) mounted at `AGENT_WORKSPACE`.

**Layout on the remote host** — pick a stable root, for example
`/opt/diagnostic-agent/`:

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

**Copy with scp / rsync** (bash, from the machine that has the output):

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

After copy, fix Compose volume paths if you flattened the layout (the generated
file expects `./workspace` next to `docker-compose.yml`).

**Start or refresh on the remote host**

```bash
ssh -i "$KEY" "$REMOTE" "cd $DEST && docker compose --env-file .env pull && docker compose --env-file .env up -d"
ssh -i "$KEY" "$REMOTE" "curl -sf http://127.0.0.1:8001/health"
```

Workspace-only updates (profile / runbooks) need a **restart or recreate** so
startup reloads YAML and rebuilds the RAG index:

```bash
ssh -i "$KEY" "$REMOTE" "cd $DEST && docker compose --env-file .env up -d --force-recreate diagnostic-agent"
```

**Checklist before declaring remote deploy done**

- [ ] `/health` → `status=ok`, `redaction_rules > 0`, expected `preset`
- [ ] `AGENT_PROMETHEUS_URL` / `AGENT_LOKI_URL` resolve **from inside the agent
      container** (Docker DNS or host gateway — not your laptop’s `localhost`)
- [ ] Alertmanager can POST the webhook URL
- [ ] Secrets present on the host only (`.env` mode 600; not in git)
- [ ] Smoke / rule-path as in [TESTING.md](TESTING.md) if this is production

---

## Graceful degradation

Soft-degrade is **opt-in** via `--allow-degraded`. Without that flag, missing
Loki, Alertmanager, or LLM config fails rather than writing a partial
deployment.

| Missing tool | Default | With `--allow-degraded` |
|---|---|---|
| **Prometheus** | **Hard fail** — aborts | **Hard fail** |
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
| Compose cannot build | Only `client/` was copied to the host | Deploy the whole fork, or use `--pull-image` |
| `diag upgrade` refuses to run | Upstream-owned files modified locally | `diag doctor --check-fork`, revert the edits, move customization under `client/` |
| SSH discovery empty | BatchMode / keys | Ensure `ssh -o BatchMode=yes user@host docker ps` works |
| Remote agent can’t reach Prom/Loki | `.env` still has laptop `localhost` or wrong Docker network | Use container DNS on shared network, or host-gateway / published ports from the agent host |
| Standalone `diag serve` ignores workspace | `AGENT_WORKSPACE` unset / wrong cwd | Export `AGENT_WORKSPACE` to the directory that contains `agent.yaml` |
| Windows console Unicode errors | Old build | Use current release (ASCII status markers) |

---

## Quick recipe card

```bash
# 0) Get `diag` — host venv OR one-shot Docker
./scripts/bootstrap-venv.sh && source .venv/bin/activate
#    or: docker run --rm -v "$PWD:/work" -w /work --network host python:3.12-slim \
#          bash -c 'pip install -q -e . && diag init --accept-defaults --allow-degraded'

# 1) Scaffold, then start
diag init --dry-run
diag init
cp client/agent/.env.example client/agent/.env
./client/scripts/start.sh
curl -sf http://127.0.0.1:8001/health

# 2) Fill the workspace from live evidence
diag scan -w client/workspace --out ./scan-evidence.json
diag draft -w client/workspace --bundle ./scan-evidence.json --out ./diag-draft
# review ./diag-draft, merge into client/workspace/, then:
diag validate -w client/workspace && diag lint -w client/workspace
diag drift -w client/workspace   # or --bundle ./scan-evidence.json --no-oracle

# 3) Non-interactive / CI
diag init --non-interactive --yes \
  --prometheus-url http://prometheus:9090 \
  --loki-url http://loki:3100 \
  --preset generic-prometheus \
  --chat-provider ollama

# 4) Upgrade
diag doctor --check-fork
git fetch upstream --tags && diag upgrade --target v1.2.0 && ./client/scripts/start.sh

# 5) Throwaway bundle (appendix; no fork lifecycle)
diag install --output ./deploy && cat ./deploy/APPLY.md
```
