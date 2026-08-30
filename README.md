# diagnostic-agent

**An alert fires. Before anyone wakes up, your metrics, your logs, and your
runbooks have already been read — and a diagnosis is waiting.**

[![CI](https://github.com/mskrado/diagnostic-agent/actions/workflows/ci.yml/badge.svg?branch=devel)](https://github.com/mskrado/diagnostic-agent/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

At 03:00 an alert pages a human who then does something entirely mechanical:
open the dashboard, tail the logs, remember which downstream service broke last
time, and find the runbook someone wrote a year ago. `diagnostic-agent` does
that first pass for you. It takes the Alertmanager webhook, pulls **metrics**
from Prometheus, **logs** from Loki, and the **dependency context** of the
affected service, retrieves the **runbooks** that match what it actually found
(RAG), reasons over all of it with a pluggable LLM, and returns a structured
diagnostic report — ranked hypotheses, each tied to the evidence behind it —
into Slack, PagerDuty, email, or a Grafana annotation before anyone has opened a
laptop.

It is **hypotheses only by default — no auto-remediation.** The agent's job is
to hand a human a head start, not to guess at your production. Opt-in
[runbook execution](#runbook-execution-opt-in) exists behind `AGENT_EXEC_ENABLED`
and runs only pre-approved, allowlisted actions in a locked-down sandbox. It is
off unless a host deliberately turns it on.

## Why this project exists

Most "AI for on-call" tooling asks you to ship your telemetry somewhere and
trust a black box. This one inverts that: the agent is a small service you run
next to your own stack, and everything it knows about your system lives in
**plain YAML and markdown that you own and review in git**.

| | |
|---|---|
| **Config, not code** | A workspace directory — manifest, profile YAML, runbooks — teaches the agent your stack. Adapting it to a new platform is authoring files, not writing Python |
| **Hypotheses, not actions** | Read-only by default. Every claim in the report has to cite metrics or logs the agent was actually given |
| **Fail-closed everywhere** | [Redaction](#redaction-is-fail-closed) is mandatory (the agent refuses to start with zero rules), routing and execution default to off, and ambiguity escalates to a human instead of guessing |
| **Your data stays yours** | Point it at a local Ollama model and nothing leaves the host; point it at any LangChain provider if you prefer |
| **Knowledge that compounds** | The runbooks you write are the agent's reasoning material — and they keep working for humans too. See [the corpus is the product](#the-corpus-is-the-product-help-it-grow) |

Deploying means holding a **client fork** of this repository: upstream product
code plus your deployment under `client/`. Run `diag init` to scaffold compose,
workspace, and start scripts; pull updates with `diag upgrade`. The single
install and upgrade guide is **[docs/INSTALL.md](docs/INSTALL.md)**.

## See it work in two minutes

No Prometheus, no Loki, no cluster — the repository ships a
[hello-world workspace](examples/hello-world/) you can point the agent at and
fire a synthetic alert into. Jump to
[Try it locally](#try-it-locally-hello-world-workspace), or read
[Architecture](#architecture) for the pipeline it runs.

## The corpus is the product: help it grow

The pipeline is the easy half. What decides whether a diagnosis is *useful* is
the **runbook corpus** it retrieves from: the accumulated "when Postgres pool
saturation looks like this, check that" knowledge that normally lives in senior
engineers' heads and in Slack threads nobody can find again.

That knowledge is the part that compounds. Every runbook added to the corpus is
one more failure mode the agent can recognise, cite, and explain — for everyone
who deploys it, not just its author. A contributed runbook about Kafka consumer
lag makes the agent better at Kafka for every stack that ever pulls the image,
and it remains a perfectly good human-readable runbook besides. The reference
corpus already covers connection-pool exhaustion, Redis outages, JVM GC
pressure, gateway 5xx, disk pressure, Elasticsearch degradation, SMTP, S3, and
third-party API failures — the long tail of "what actually breaks" is where the
contributions matter most.

**This is the contribution we want most, and it needs no LLM credentials to
review.** The loop is three files:

1. A **runbook** under `runbooks/` (copy `runbooks/_TEMPLATE-runbook.md`) —
   hypotheses and the queries an operator should paste into Grafana
2. A **blind-eval case** in `eval/blind_eval_dataset.yaml` — synthetic logs plus
   the ground-truth root cause, so the diagnosis can be scored offline
3. A **scenario** in `runbook_scenarios.yaml` — the alert labels that should
   surface it

CI then lints all three on every PR without touching a model: schema,
runbook↔scenario pairing, eval grounding, hypotheses-only wording. If you have
ever written an incident postmortem, you already have the raw material — and
`incident-YYYY-MM-DD-<slug>.md` write-ups are welcome corpus entries too.

Good first contributions, in rough order of appetite: a runbook for a failure
mode you have actually debugged · a preset for a stack that is not Spring
(`generic-prometheus` and `spring-micrometer` ship today) · an example workspace
· a `logs_profile.yaml` for a log format we do not parse well yet.

Start at [CONTRIBUTING.md](CONTRIBUTING.md) and
[runbooks/README.md](runbooks/README.md) for the authoring rules; the branch and
review mechanics are in [docs/SDLC_GUIDE.md](docs/SDLC_GUIDE.md).

## Contents

Topics in this README:

- [Quick start](#quick-start) — `diag init` scaffolds `client/` in your private repo copy
- [Try it locally (hello-world workspace)](#try-it-locally-hello-world-workspace) — run the agent against the bundled example
- [Host workspace](#host-workspace) — manifest, profile files, preset chain, [fail-closed redaction](#redaction-is-fail-closed)
- [Architecture](#architecture) — alert → LangGraph pipeline → route → report, plus [routing](#routing-opt-in) and [runbook execution](#runbook-execution-opt-in)
- [Configuration](#configuration) — every `AGENT_` environment variable
- [Tools](#tools) — `diag validate` / `lint` / `doctor` / `e2e` / `eval` / `replay` / `serve`
- [Develop](#develop) — local environment and test run
- [License](#license) · [Contributing](#contributing)

Additional documentation:

| Document | Covers |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | **The install guide**: private copy, dependencies and `diag` bootstrap, `diag init`, start, wire, verify, `diag upgrade`, air gap, runtimes, throwaway bundles, troubleshooting |
| [docs/WORKSPACE.md](docs/WORKSPACE.md) | Workspace reference: discovery order, `agent.yaml` keys, flat layout, precedence, CI validation |
| [docs/INTEGRATING.md](docs/INTEGRATING.md) | Manual wiring without the generator: hand-written workspace, Alertmanager, Compose snippet, CI guard |
| [docs/TESTING_STRATEGY.md](docs/TESTING_STRATEGY.md) | Testing strategy: the ten test layers, configuration matrix, gates, and how to run checks against a production agent |
| [docs/TESTING.md](docs/TESTING.md) | Operator E2E: smoke-test, remote rule-path, runbook-e2e wrappers; host vs agent ownership |
| [runbooks/README.md](runbooks/README.md) | RAG corpus: chunking and retrieval behaviour, file layout, runbook authoring rules |
| [eval/README.md](eval/README.md) | Blind eval and routing replay: how cases are scored offline |
| [docs/design/sandboxed-execution.md](docs/design/sandboxed-execution.md) | Execution design: threat model, invariants, sandbox/classifier contracts, implementation status |
| [docs/SDLC_GUIDE.md](docs/SDLC_GUIDE.md) | Contribution lifecycle: environments, branching, issue workflow, CI/CD, release |
| [SECURITY.md](SECURITY.md) | Supported versions, vulnerability reporting, threat model notes |
| [CONTRIBUTING.md](CONTRIBUTING.md) · [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | How to propose changes and the community expectations |
| [`examples/hello-world/`](examples/hello-world/) · [`examples/spring-modular-monolith/`](examples/spring-modular-monolith/) | Reference workspaces to copy and adapt |

## Quick start

Mirror-clone this repo into your organization, then on the deployment host:

```bash
./scripts/install-system-deps.sh   # OS packages (skip if Python >=3.11 already works)
./scripts/bootstrap-venv.sh        # .venv from requirements.lock + `diag`
source .venv/bin/activate
diag init
cp client/agent/.env.example client/agent/.env   # fill secrets
./client/scripts/start.sh
```

Full lifecycle — private copy, dependency bootstrap, Docker-only init on Amazon
Linux 2, wiring, upgrades, air gap, and throwaway `diag install` bundles:
**[docs/INSTALL.md](docs/INSTALL.md)**.

## Try it locally (hello-world workspace)

```bash
pip install -e ".[dev]"

export AGENT_PROMETHEUS_URL=http://localhost:9090
export AGENT_LOKI_URL=http://localhost:3100
# LLM — pick one provider (see Configuration)
export AGENT_CHAT_PROVIDER=ollama
export AGENT_CHAT_MODEL=mistral:7b-instruct

diag validate -w examples/hello-world
diag serve -w examples/hello-world --port 8000
```

Docker — mount your workspace at `/workspace` and every command finds it:

```bash
docker build -t diagnostic-agent:local .
docker run --rm -p 8001:8000 \
  -e AGENT_PROMETHEUS_URL=http://host.docker.internal:9090 \
  -e AGENT_LOKI_URL=http://host.docker.internal:3100 \
  -v "$PWD/examples/hello-world:/workspace:ro" \
  diagnostic-agent:local

# same image, no server: check a workspace in CI
docker run --rm -v "$PWD/examples/hello-world:/workspace:ro" \
  diagnostic-agent:local diag validate
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

## Host workspace

A workspace is one directory in your repository holding everything specific to
your stack. An `agent.yaml` manifest declares where each piece lives, so
commands take no path arguments:

```
infrastructure/diagnostic-agent/
├── agent.yaml        # schema, pinned agent version, preset, paths
├── profile/          # the integration profile (table below)
├── runbooks/         # RAG corpus (markdown)
├── scenarios.yaml    # runbook E2E scenarios
└── blind_eval.yaml   # blind-eval dataset
```

```yaml
schema: 1
agent_version: 0.1.0
extends: spring-micrometer
```

The profile directory supplies:

| File | Purpose |
|---|---|
| `service_map.yaml` | Topology / blast radius |
| `metrics_profile.yaml` | PromQL templates (`{service}`, `{window}`) |
| `logs_profile.yaml` | Loki label, level filter, alert line filters, optional module regex |
| `redaction.yaml` | Ordered regex redaction rules |
| `prompt_profile.yaml` | Platform description + tool-run hints |

Every key is optional — a directory following the conventional layout resolves
identically, and tools skip checks whose inputs you have not supplied yet. See
**[docs/WORKSPACE.md](docs/WORKSPACE.md)** for the full reference.

Config precedence: **env vars > workspace manifest > profile files > built-in presets**.

Built-in presets (shipped in-package):

- `generic-prometheus` — community `http_requests_total` naming. Every preset
  chain is rooted here, so a partial preset can never resolve a section to nothing.
- `spring-micrometer` — Spring Boot Micrometer (`http_server_requests_seconds_*`, HikariCP, JVM)

Presets carry naming conventions, not topology: `service_map.yaml` comes from
your profile only. **Two presets is not enough** — a preset for your stack's
metric naming is one of the highest-leverage contributions available.

### Redaction is fail-closed

`redaction.yaml` rules **accumulate** across an `extends:` chain — declare
`extends: generic-prometheus` and your rules are appended to the base secret
scrubbing. Reuse a parent rule's `name` to override it.

The agent refuses to start when the resolved profile has zero redaction rules,
so a mis-pointed workspace fails loudly instead of quietly emitting raw data.
`diag validate` and `GET /health` both report the count. Set
`AGENT_REQUIRE_REDACTION=false` to opt out deliberately.

Reference examples:

- [`examples/hello-world/`](examples/hello-world/) — minimal 3-tier app
- [`examples/spring-modular-monolith/`](examples/spring-modular-monolith/) — Spring Boot modular monolith (Micrometer, tenant redaction, rich topology)

See **[docs/INSTALL.md](docs/INSTALL.md)** to generate a workspace, or
[docs/INTEGRATING.md](docs/INTEGRATING.md) to hand-write one.

## Architecture

```
Prometheus alert ──▶ Alertmanager ──▶ POST /alert
                                         │
                                   LangGraph:
                     detect → retrieve → rag_lookup → correlate → report
                                                                    │
                                                             should_route()
                        ┌───────────────────────┬───────────────────┴────────┐
                        ▼                       ▼                            ▼
                     report                 escalate                     execute
                        │                       │                            │
                        │                       │                    execute_runbook
                        │                       │                  (classifier → sandbox)
                        └───────────────────────┴────────────┬───────────────┘
                                                             │
              audit JSONL + optional Slack / PagerDuty / email / Grafana annotation
```

Delivery runs after the graph completes, on every route. Which channels fire
depends on the route: PagerDuty opens an incident on `escalate`, Slack and email
post the reasoning trace whenever they are enabled.

### Deployment model: one agent per stack

The agent is an **independent service** — it holds no knowledge of any
application and reaches its data sources over plain HTTP. It is not, however, a
multi-tenant service: **one running instance serves one stack.**

That is deliberate. The profile, dependency map, redaction rules, RAG index, and
LLM client are resolved once at startup from a single `AGENT_WORKSPACE` and
cached for the process lifetime. Only the per-alert `service` label varies
between requests.

| | |
|---|---|
| **What you get** | Credentials, redaction rules, and the runbook index are scoped to one stack — a misconfigured workspace cannot leak another team's data or retrieve their runbooks |
| **What it costs** | Several stacks means several instances, each with its own workspace, `.env`, and webhook URL |
| **Serving many stacks from one process would need** | Per-alert profile routing, per-tenant RAG collections, per-stack credentials, and a tenant identity on the webhook — none of which exist today |

Run one agent next to each observability stack, and give each its own workspace.
See [docs/INSTALL.md](docs/INSTALL.md#run-the-agent-docker-image-or-standalone-process)
for the runtime options.

### Routing (opt-in)

Routing is off by default (`AGENT_ROUTING_ENABLED=false`), in which case
`should_route` always returns `report` and the agent behaves exactly like the
linear read-only pipeline. Once enabled:

| Condition | Route |
|---|---|
| Severity normalizes to SEV1 or SEV2 | `escalate` |
| `confidence_note: low` | `escalate` |
| `confidence_note: high` **and** runbook context was retrieved | `execute` |
| Anything else | `report` |

Host severity strings normalize onto SEV1–SEV4 (`critical`/`p1`/`fatal` → SEV1,
`warning`/`warn`/`medium` → SEV3, …); anything unrecognized becomes `UNKNOWN`
and never escalates on severity alone. The decision is recorded as
`route_decision` in the report and the audit record, so you can enable routing
and observe the decisions before enabling execution.

### Runbook execution (opt-in)

The `execute` route reaches the `execute_runbook` node, which is fail-closed at
every step:

1. Select an executable runbook — one that declares a
   [`runbook-actions` block](runbooks/README.md#executable-steps-runbook-actions)
   matching the alert type, service, and confidence. Zero or multiple matches
   escalate.
2. Resolve the step's `action_id` in
   [`execution_profile.yaml`](docs/WORKSPACE.md#execution_profileyaml-optional).
   Presets ship **zero** actions, so an unconfigured host has nothing to run.
3. Run the destructive-action classifier. A `hold` verdict escalates without
   ever reaching the sandbox.
4. Run the action through `Sandbox`, which refuses everything unless
   `AGENT_EXEC_ENABLED=true` and executes argv arrays (never shell strings) in a
   disposable container with no network, no mounts, dropped capabilities, a
   read-only root filesystem, and a per-action timeout.

Anything missing, ambiguous, denied, non-zero, or raised sets
`outcome: escalated`, leaving the incident to a human. Command output is
redacted at the sandbox boundary as soon as it leaves the container. Note that
the report is finalized before this node runs, so the action and its result are
currently visible only in graph state and the agent log — not in the audit
record or the Slack / PagerDuty trace.

With shipped defaults — execution disabled, no allowlisted actions, no
`runbook-actions` blocks in the corpus — the branch can only escalate. Design,
threat model, and current implementation status:
**[docs/design/sandboxed-execution.md](docs/design/sandboxed-execution.md)**.
Post-execution verification of recovery
([#53](https://github.com/mskrado/diagnostic-agent/issues/53)) is not
implemented yet, so the branch ends after the sandbox call.

## Configuration

All settings use the `AGENT_` prefix (see `.env.example`).

| Variable | Default | Notes |
|---|---|---|
| `AGENT_WORKSPACE` | `/workspace` in the image | Host workspace directory |
| `AGENT_PROFILE_DIR` | *(from workspace)* | Path to integration profile |
| `AGENT_DEFAULT_PRESET` | `generic-prometheus` | Built-in preset for `extends:` chains |
| `AGENT_REQUIRE_REDACTION` | `true` | Refuse to start with zero redaction rules |
| `AGENT_PROMETHEUS_URL` | `http://prometheus:9090` | |
| `AGENT_LOKI_URL` | `http://loki:3100` | |
| `AGENT_CHAT_PROVIDER` / `AGENT_CHAT_MODEL` | ollama / mistral | Any LangChain provider |
| `AGENT_EMBED_PROVIDER` / `AGENT_EMBED_MODEL` | ollama / nomic-embed-text | |
| `AGENT_RAG_ENABLED` | `true` | |
| `AGENT_SERVICE_MAP_PATH` | *(from profile)* | Override topology file |
| `AGENT_RUNBOOKS_PATH` | *(from profile or `./runbooks`)* | Override RAG corpus |

### Routing and execution

Every switch here defaults to off, so enabling them is always a deliberate act.

| Variable | Default | Notes |
|---|---|---|
| `AGENT_ROUTING_ENABLED` | `false` | Enable [severity routing](#routing-opt-in). Off means every alert takes the `report` route |
| `AGENT_EXEC_ENABLED` | `false` | Master switch for [runbook execution](#runbook-execution-opt-in). Off makes the sandbox refuse every action |
| `AGENT_EXEC_PROFILE_PATH` | *(from profile)* | Reported path of `execution_profile.yaml`. The allowlist itself is always read from the profile directory |
| `AGENT_EXEC_DESTRUCTIVE_PATTERNS` | *(empty)* | Extra destructive verb patterns, comma-separated, merged with the built-in list |

### Delivery

| Variable | Default | Notes |
|---|---|---|
| `AGENT_AUDIT_LOG_DIR` | `<repo>/audit` | JSONL audit records, one per diagnosis |
| `AGENT_GRAFANA_ANNOTATIONS_ENABLED` | `true` | Needs `AGENT_GRAFANA_TOKEN` |
| `AGENT_EMAIL_ENABLED` | `true` | With `AGENT_EMAIL_TO` and the `AGENT_SMTP_*` settings |
| `AGENT_EMAIL_ATTACH_AUDIT` | `true` | Attach redacted audit JSON (`llm_raw` + prompts) to each diagnostic email; set `false` for body-only |
| `AGENT_EMAIL_ATTACH_AUDIT_MAX_BYTES` | `262144` | Skip the attachment (email still sends) when the redacted JSON exceeds this size |
| `AGENT_SLACK_ENABLED` | `false` | Posts the reasoning trace; needs `AGENT_SLACK_WEBHOOK_URL`. Optional `AGENT_SLACK_CHANNEL`, `AGENT_SLACK_USERNAME` |
| `AGENT_PAGERDUTY_ENABLED` | `false` | Needs `AGENT_PAGERDUTY_API_TOKEN` and `AGENT_PAGERDUTY_FROM_EMAIL`; `AGENT_PAGERDUTY_SERVICE_ID` to open incidents |

PagerDuty opens an incident when the route is `escalate`. When the alert already
carries an incident id and the diagnosis is high-confidence, it appends a note
instead. All delivered text passes through the redaction rules.

## Tools

Every command runs against a workspace, so host projects use the published
image rather than writing their own scripts.

| Command | Purpose |
|---|---|
| `diag init` | Scaffold a client deployment under `client/` ([INSTALL.md](docs/INSTALL.md)) |
| `diag upgrade` | Merge an upstream release into your client fork |
| `diag install` | Discover a stack and generate a throwaway bundle ([INSTALL.md](docs/INSTALL.md#appendix-throwaway-bundles-diag-install)) |
| `diag scan` | Report what a live stack exposes — services, metric naming, alerts, log shape ([SCAN.md](docs/SCAN.md)) |
| `diag draft` | Draft workspace files from that evidence, writing only what the stack confirms ([DRAFT.md](docs/DRAFT.md)) |
| `diag mine-eval` | Draft blind-eval cases from redacted audit logs ([DRAFT.md](docs/DRAFT.md#diag-mine-eval--blind-eval-cases-from-audits)) |
| `diag drift` | Gate workspace drift against a live stack or scan bundle ([DRIFT.md](docs/DRIFT.md)) |
| `diag validate` | Manifest schema, profile resolution, redaction rule count, topology parse |
| `diag lint` | Corpus lint: runbook/scenario coverage, blind-eval grounding, hypotheses-only framing |
| `diag doctor` | Probe connectivity; `--check-fork` verifies no upstream-path drift |
| `diag health-check` | Report the resolved workspace and profile health without a running server |
| `diag e2e --url` | POST every scenario at a running agent and assert the report + redaction |
| `diag eval blind` | Score LLM root-cause identification against the workspace dataset |
| `diag replay` | Replay scenarios through the routing logic and score route decisions — no LLM, no stack ([eval/README.md](eval/README.md#routing-replay-eval-diag-replay)) |
| `diag serve` | Run the `/alert` webhook server |

Operator wrappers for host stacks (smoke, remote rule-path, runbook e2e) live
under [`scripts/`](scripts/) — see [docs/TESTING.md](docs/TESTING.md).

## Develop

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"   # Windows
# pip install -e ".[dev]"               # Unix
pytest -q
```

`pyproject.toml` reads its dependency lists from `requirements.txt` (runtime) and
`requirements-dev.txt` (tests), so `pip install -r` and `pip install .[dev]`
cannot drift apart.

This repository is itself a workspace — `runbooks/`, `runbook_scenarios.yaml`,
and `eval/blind_eval_dataset.yaml` resolve through the same conventions a host
uses — so CI exercises the host-facing commands rather than private test paths.
That also means a corpus contribution is testable the moment you write it:
`diag lint` is the same check CI runs, and it needs no model and no stack. See
`eval/README.md` for the blind-eval workflow.

## License

Apache-2.0 — see [LICENSE](LICENSE).

## Contributing

Contributions are genuinely welcome, and the highest-value one is the easiest to
review: a **runbook + eval case + scenario** that CI can lint without LLM
credentials. Every one of them permanently widens the set of failures the agent
can explain — see [the corpus is the product](#the-corpus-is-the-product-help-it-grow).

Start with [CONTRIBUTING.md](CONTRIBUTING.md) and the shared lifecycle guide
**[docs/SDLC_GUIDE.md](docs/SDLC_GUIDE.md)** (issue → `feature/<slug>-<n>` → `devel` →
release). Bug reports, missing presets, and rough edges in the install path are
all worth an issue.
