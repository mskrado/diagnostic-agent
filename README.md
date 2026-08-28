# diagnostic-agent

A **config-driven**, reactive diagnostic agent for Prometheus / Alertmanager.

When an alert fires, the agent pulls **metrics** (Prometheus), **logs** (Loki),
and **dependency context**, retrieves relevant **runbooks** (RAG), reasons with
a pluggable LLM, and emits a structured diagnostic report.

**Hypotheses only by default — no auto-remediation.** Opt-in
[runbook execution](#runbook-execution-opt-in) exists behind `AGENT_EXEC_ENABLED`
and runs only pre-approved, allowlisted actions in a locked-down sandbox. It is
off unless a host deliberately turns it on.

Integrating means holding a **client fork** of this repository: upstream product
code plus your deployment under `client/`. Run `diag init` to scaffold compose,
workspace, and start scripts; pull updates with `diag upgrade`.
See **[docs/CLIENT_FORK.md](docs/CLIENT_FORK.md)**.

For throwaway bundles or CI-only workspaces without a fork, use `diag install`
or mount an example workspace — see [docs/INTEGRATING.md](docs/INTEGRATING.md).

## Contents

Topics in this README:

- [Quick start — client fork](#quick-start--client-fork) — `diag init` scaffolds `client/` in your private repo copy
- [Quick start — install bundle](#quick-start--install-bundle-throwaway) — generate a throwaway bundle with `diag install`
- [Quick start (hello-world workspace)](#quick-start-hello-world-workspace) — run the agent locally or in Docker against the bundled example
- [Host workspace](#host-workspace) — manifest, profile files, preset chain, [fail-closed redaction](#redaction-is-fail-closed)
- [Architecture](#architecture) — alert → LangGraph pipeline → route → report, plus [routing](#routing-opt-in) and [runbook execution](#runbook-execution-opt-in)
- [Configuration](#configuration) — every `AGENT_` environment variable
- [Tools](#tools) — `diag validate` / `lint` / `doctor` / `e2e` / `eval` / `replay` / `serve`
- [Develop](#develop) — local environment and test run
- [License](#license) · [Contributing](#contributing)

Additional documentation:

| Document | Covers |
|---|---|
| [docs/CLIENT_FORK.md](docs/CLIENT_FORK.md) | **Client fork model**: private repo copy, `diag init`, start scripts, `diag upgrade`, offline packs |
| [docs/INSTALL.md](docs/INSTALL.md) | `diag install` end to end: interactive vs non-interactive, every collected parameter, generated files, troubleshooting |
| [docs/WORKSPACE.md](docs/WORKSPACE.md) | Workspace reference: discovery order, `agent.yaml` keys, flat layout, precedence, CI validation |
| [docs/INTEGRATING.md](docs/INTEGRATING.md) | Onboarding a host project: distribution choice, Alertmanager wiring, Compose snippet, verification, CI guard |
| [docs/TESTING_STRATEGY.md](docs/TESTING_STRATEGY.md) | Testing strategy: the ten test layers, configuration matrix, gates, and how to run checks against a production agent |
| [docs/TESTING.md](docs/TESTING.md) | Operator E2E: smoke-test, remote rule-path, runbook-e2e wrappers; host vs agent ownership |
| [runbooks/README.md](runbooks/README.md) | RAG corpus: chunking and retrieval behaviour, file layout, runbook authoring rules |
| [eval/README.md](eval/README.md) | Blind eval and routing replay: how cases are scored offline |
| [docs/design/sandboxed-execution.md](docs/design/sandboxed-execution.md) | Execution design: threat model, invariants, sandbox/classifier contracts, implementation status |
| [docs/SDLC_GUIDE.md](docs/SDLC_GUIDE.md) | Contribution lifecycle: environments, branching, issue workflow, CI/CD, release |
| [SECURITY.md](SECURITY.md) | Supported versions, vulnerability reporting, threat model notes |
| [CONTRIBUTING.md](CONTRIBUTING.md) · [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | How to propose changes and the community expectations |
| [`examples/hello-world/`](examples/hello-world/) · [`examples/spring-modular-monolith/`](examples/spring-modular-monolith/) | Reference workspaces to copy and adapt |

## Quick start — client fork

Mirror-clone this repo into your organization, then on the deployment host:

```bash
pip install -e ".[dev]"
diag init
cp client/agent/.env.example client/agent/.env   # fill secrets
./client/scripts/start.sh
```

Full lifecycle (private copy, upgrades, air gap): **[docs/CLIENT_FORK.md](docs/CLIENT_FORK.md)**

## Quick start — install bundle (throwaway)

Discover running observability tools and generate a complete agent + wiring
bundle. Full guide (interactive vs non-interactive, every parameter):  
**[docs/INSTALL.md](docs/INSTALL.md)**

```bash
pip install -e ".[dev]"
diag install --output ./deploy
# then follow ./deploy/APPLY.md
```

## Quick start (hello-world workspace)

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
your profile only.

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

See **[docs/INTEGRATING.md](docs/INTEGRATING.md)** for a complete onboarding guide.

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
| `diag init` | Scaffold a client deployment under `client/` ([CLIENT_FORK.md](docs/CLIENT_FORK.md)) |
| `diag upgrade` | Merge an upstream release into your client fork |
| `diag install` | Discover a stack and generate a throwaway bundle ([INSTALL.md](docs/INSTALL.md)) |
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
See `eval/README.md` for the blind-eval workflow.

## License

Apache-2.0 — see [LICENSE](LICENSE).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and the shared lifecycle guide
**[docs/SDLC_GUIDE.md](docs/SDLC_GUIDE.md)** (issue → `feature/<slug>-<n>` → `devel` →
release). The preferred contribution is a **runbook + eval case + scenario** that
CI can lint without LLM credentials.
