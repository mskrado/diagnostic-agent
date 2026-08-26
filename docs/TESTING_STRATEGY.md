# Testing strategy

How this agent is verified, from a unit test on a laptop to a check fired against
a **production** deployment. This document is the *strategy*: what each kind of
test proves, what must be configured for it to run, and which ones are safe to
point at production. For copy-paste operator recipes see
[TESTING.md](TESTING.md).

The agent is unusual in two ways that shape the strategy:

1. **It is config-driven.** Most production defects are workspace/profile
   defects (wrong PromQL, missing redaction rule, unmatched runbook), not Python
   defects. So configuration and corpus checks are first-class test layers, not
   linting afterthoughts.
2. **It calls an LLM.** Output is non-deterministic, so correctness is measured
   statistically (eval scores over a case set), while the pipeline around it is
   asserted deterministically (schema, redaction, routing, delivery).

---

## Topics

1. [Layer map](#1-layer-map)
2. [Layers in detail](#2-layers-in-detail)
3. [Configuration matrix](#3-configuration-matrix)
4. [Running against production](#4-running-against-production)
5. [Gates and cadence](#5-gates-and-cadence)
6. [Pass criteria and triage](#6-pass-criteria-and-triage)
7. [Ownership: agent repo vs host repo](#7-ownership-agent-repo-vs-host-repo)
8. [Known gaps](#8-known-gaps)

---

## 1. Layer map

| # | Layer | Proves | LLM | Running stack | Primary gate |
|---|---|---|---|---|---|
| L1 | **Unit** (`pytest`) | Python behaviour: schema coercion, redaction, PromQL/LogQL builders, routing, sandbox, delivery formatting | no | no | Every PR |
| L2 | **Workspace validation** (`diag validate`) | Manifest schema, profile resolution, redaction rule count > 0, topology parses | no | no | Every PR (agent + host) |
| L3 | **Corpus lint** (`diag lint`) | Every runbook has a scenario and vice versa; eval tokens appear in their logs; hypotheses-only framing | no | no | Every PR (agent + host) |
| L4 | **Routing replay** (`diag replay`) | Alert → `report` / `escalate` / `execute` decisions and runbook selection | no | no | Every PR; before enabling routing/exec |
| L5 | **Connectivity doctor** (`diag doctor`) | Agent can actually reach Prometheus, Loki, Grafana, SMTP from where it runs | no | yes (targets) | Post-deploy |
| L6 | **Diagnostic quality eval** (`diag eval blind`) | How well the model finds root cause from logs; RAG contribution; calibration | **yes** | offline: no · live: yes | Pre-release; model/prompt changes |
| L7 | **Integration smoke** (`scripts/smoke-test`) | `/health`, alert ingestion, audit write, redaction in audit, email delivery, Grafana annotation | yes | yes | Post-deploy |
| L8 | **Rule-path E2E** (`-RulePath`, `scripts/prod-rulepath-e2e`) | The *whole* reactive chain: log line → Promtail → Loki ruler → Alertmanager route → agent webhook → audit | yes | yes | Post-deploy; after alerting config change |
| L9 | **Scenario E2E** (`diag e2e`, `scripts/runbook-e2e`) | Every workspace scenario returns a well-formed report for the right service/alert, with no redaction leak | yes | yes | Post-deploy; workspace change |
| L10 | **Fault injection** (`-RealPath`) | A real broken dependency produces a real alert and a real diagnosis | yes | yes | Non-production only |

Rules of thumb:

- **L1–L4 are free and deterministic.** Run them on every change; never skip them
  because a higher layer passed.
- **L5–L9 need a deployment.** They prove wiring, which unit tests structurally
  cannot.
- **L6 is statistical.** Compare scores between runs; a single case flipping is
  noise, a trend is signal.
- **L10 breaks things.** It belongs in DEV/staging, not production.

---

## 2. Layers in detail

### L1 — Unit tests

Deterministic tests over the Python surface: `tests/` (35 modules) covering the
diagnosis schema and its repair path, redaction, Loki/Prometheus query building,
routing, the sandbox/classifier, delivery (email, Slack, PagerDuty, Grafana),
install collection, and workspace resolution.

```bash
pip install -e ".[dev]"
pytest -q
```

Configure: nothing. `tests/conftest.py` pins the bundled
`examples/spring-modular-monolith` profile so tests do not depend on a host
workspace or credentials.

Does **not** prove: that your PromQL matches the metrics your host actually
exports, or that the LLM is any good.

### L2 — Workspace validation

Checks the *configuration contract* between a host and the agent: manifest keys,
profile/preset resolution chain, that redaction resolves to ≥1 rule
(`AGENT_REQUIRE_REDACTION=true` makes an empty ruleset fail closed at startup),
and that the service topology parses.

```bash
diag validate -w examples/hello-world
docker run --rm -v "$PWD/infrastructure/diagnostic-agent:/workspace:ro" \
  ghcr.io/mskrado/diagnostic-agent:<pinned-tag> diag validate
```

Configure: a workspace directory. No credentials, no network.

### L3 — Corpus lint

Content checks over runbooks, scenarios, and eval cases: coverage in both
directions (runbook without scenario, scenario without runbook), blind-eval
grounding (expected keywords actually appear in the case's injected logs), and
the hypotheses-only framing invariant.

```bash
diag lint
diag lint -w /path/to/host/workspace
```

Configure: workspace with `runbooks/`, `scenarios.yaml`, and (optionally) a blind
eval dataset.

### L4 — Routing replay

Replays each scenario through the real `should_route` with a simulated
confidence, asserting the route and the selected runbook. No LLM, no stack — so
it is the guard to run *before* enabling routing or execution on a host. Routing
is forced on for the run and restored after, so results do not depend on
`AGENT_ROUTING_ENABLED`.

```bash
diag replay
diag replay --only redis-connection-errors
```

Configure: `scenarios.yaml`, optionally with a `replay:` block pinning
`expected_route` / `expected_runbook` / `confidence_note`. Details:
[eval/README.md](../eval/README.md#routing-replay-eval-diag-replay).

Does **not** prove: that an action is safe to execute — only that it would be
routed as intended.

### L5 — Connectivity doctor

Probes Prometheus, Loki, Grafana, and SMTP from the agent's own vantage point.
Run it **inside** the deployed container: reachability from a laptop through a
tunnel says nothing about in-network DNS.

```bash
docker exec <agent-container> diag doctor
```

Configure: `AGENT_PROMETHEUS_URL`, `AGENT_LOKI_URL`, `AGENT_GRAFANA_URL`,
`AGENT_GRAFANA_TOKEN`, SMTP settings — i.e. whatever the deployment already sets.

### L6 — Diagnostic quality eval (blind eval)

Measures the model's ability to name a root cause **from logs alone**, with RAG
forced off, scored against independent ground truth in
`blind_eval_dataset.yaml`. Offline mode needs only LLM credentials; live mode
pushes the case logs into Loki and fires a real `/alert` so the full retrieval
path is exercised.

```bash
# Offline — fastest, no stack
diag eval blind --limit 3
diag eval blind --judge

# Live — inject into Loki, then POST /alert at a running agent
diag eval blind --live-url http://localhost:8001 --loki-url http://localhost:3100
```

The workspace flag belongs to `eval`, before the `blind` subcommand:

```bash
python -m app.cli eval -w ./deploy/publishi.ai/agent/workspace blind --limit 3
```

Configure: `AGENT_CHAT_PROVIDER` / `AGENT_CHAT_MODEL` plus provider credentials
(`OPENAI_API_KEY`, AWS chain for Bedrock, …). For a *truly* blind live run start
the agent with RAG disabled (`AGENT_RAG_ENABLED=false`); the result JSON records
`rag_used` so you can confirm it.

Read the metrics as a set: `identified_accuracy`, `mean_keyword_recall`,
`grounded`, `confidence_note` calibration, and `mean_judge_score` with
`--judge`. Full reference: [eval/README.md](../eval/README.md).

Does **not** prove: delivery, redaction in transit, or alerting wiring.

### L7 — Integration smoke

Exercises a deployed agent end to end at the HTTP boundary: `/health`
(`status=ok`, `agent_initialized=true`), alert ingestion (via Alertmanager by
default, or `POST /alert` with `-DirectAgent`), a new audit JSONL record, **no
tenant identifiers in that audit line**, both emails in Mailpit, and the Grafana
annotation when a token is configured.

```powershell
.\scripts\smoke-test.ps1 -ContainerPrefix publishi -DirectAgent
```

```bash
./scripts/smoke-test.sh -ContainerPrefix publishi
```

Configure: `-ContainerPrefix` (or `AGENT_E2E_CONTAINER_PREFIX`) matching the host
compose names, `-AgentUrl`, and — for the default path — Alertmanager + Mailpit.
`-SkipGrafana` / `-SkipMailpit` drop optional checks.

### L8 — Rule-path E2E

The only layer that proves the *reactive* chain a real incident travels:
a sentinel log line is emitted from a throwaway container, Promtail scrapes it,
the Loki ruler evaluates a marker rule, Alertmanager routes it, and the agent
writes a `DiagnosticAgentSmokeMarker` audit record.

Locally the smoke script drives it; for a remote host use the SSH wrapper,
because `-RulePath` talks to the **local** Docker daemon and HTTP tunnels cannot
carry `docker run` / `docker exec`:

```powershell
.\scripts\smoke-test.ps1 -ContainerPrefix publishi -RulePath

.\scripts\prod-rulepath-e2e.ps1 `
  -SshTarget ec2-user@YOUR_HOST `
  -IdentityFile $HOME\.ssh\your-key.pem `
  -ContainerPrefix publishi
```

Configure: Promtail scraping container stdout, a Loki ruler group defining the
marker rule, an Alertmanager route delivering `severity=warning` to the agent
webhook, and SSH access for the remote variant. Default timeout is 420s to cover
Alertmanager `group_interval` on repeat runs. The marker rule stays inert in
normal operation, which is what makes this safe to re-run.

### L9 — Scenario E2E

POSTs every workspace scenario at a running agent and asserts the returned
report: `service`, `alert_type`, and `severity` echo the alert; `diagnosis` and
`evidence` are present; and the seeded tenant probes
(`tenant-smoke-test`, a tenant UUID) do **not** appear anywhere in the response.

```bash
docker compose exec diagnostic-agent diag e2e --url http://localhost:8000
diag e2e --url http://localhost:8001 --only high-error-rate
python scripts/runbook-e2e.py -w /path/to/workspace --mode all
```

Configure: workspace `scenarios.yaml`; a reachable agent URL. `--mode offline`
in the wrapper runs L2+L3 only; `--mode live` runs this layer.

### L10 — Fault injection

Stops a real dependency container and waits for the genuine alert rule to fire
and produce an audit record. This is the only layer that validates the host's
*production* alert rules rather than a synthetic marker.

```powershell
.\scripts\smoke-test.ps1 -ContainerPrefix publishi -RealPath
```

**Never run against production.** It causes real downtime and real pages.

---

## 3. Configuration matrix

Agent runtime settings (all `AGENT_*`, see [Configuration in the
README](../README.md#configuration)) that materially change test behaviour:

| Setting | Why it matters for testing |
|---|---|
| `AGENT_PROMETHEUS_URL` / `AGENT_LOKI_URL` | L5/L7/L9 retrieve real data; wrong values give empty snapshots and vague diagnoses |
| `AGENT_GRAFANA_URL` + `AGENT_GRAFANA_TOKEN` | Annotation assertion in L7; empty token degrades gracefully (skipped, not failed) |
| `AGENT_CHAT_PROVIDER` / `AGENT_CHAT_MODEL` | L6–L9 cost and quality; weak models produce partial structured output |
| `AGENT_CHAT_MAX_TOKENS` | Too low truncates the `Diagnosis` tool call |
| `AGENT_RAG_ENABLED` | Must be `false` for a blind live eval; `true` to measure the runbook delta |
| `AGENT_REQUIRE_REDACTION` | Keep `true` so a mis-mounted workspace fails closed instead of leaking |
| `AGENT_ROUTING_ENABLED` | Gates route recording; L4 forces it on internally regardless |
| `AGENT_EXEC_ENABLED` | Must stay `false` unless you are deliberately testing sandboxed execution |
| `AGENT_AUDIT_LOG_DIR` | L7/L8 assert new lines here (`/app/audit/diagnostics-<date>.jsonl` in the image) |

Harness-only variables used by `scripts/*`:

| Variable | Used by |
|---|---|
| `AGENT_E2E_CONTAINER_PREFIX` | Compose name prefix for smoke / rule-path scripts |
| `AGENT_E2E_SSH_TARGET` / `AGENT_E2E_SSH_IDENTITY` | `prod-rulepath-e2e` |
| `AGENT_E2E_URL` / `AGENT_E2E_WORKSPACE` | `runbook-e2e` defaults |
| `DIAGNOSTIC_AGENT_IMAGE` | Image tag `runbook-e2e` runs `diag` from |
| `AGENT_GRAFANA_TOKEN` / `DIAGNOSTIC_AGENT_GRAFANA_TOKEN` | Annotation check in smoke-test |

Workspace files each layer depends on:

| File | Layers |
|---|---|
| `agent.yaml` + profile files | L2, L5, L7–L9 |
| `redaction.yaml` | L2 (count), L7 (audit), L9 (response probes) |
| `runbooks/` | L3, L6 (RAG-on comparison) |
| `scenarios.yaml` | L3, L4, L9 |
| `blind_eval_dataset.yaml` | L3 (grounding), L6 |

---

## 4. Running against production

### 4.1 Blast-radius classification

Decide with this table before running anything at a production agent.

| Check | Reads | Writes | Notifies | Prod-safe |
|---|---|---|---|---|
| `GET /health` | agent state | — | — | **Yes** |
| `diag doctor` (in container) | Prom/Loki/Grafana/SMTP | — | — | **Yes** |
| `diag validate` / `diag lint` / `diag replay` | workspace files | result JSON (replay) | — | **Yes** |
| `diag e2e` (L9) | Prom/Loki | audit records, Grafana annotations | email/Slack/PagerDuty **if routed** | Yes, with caveats |
| `smoke-test -DirectAgent` (L7) | Prom/Loki | audit record, annotation | diagnostic email | Yes, with caveats |
| `smoke-test` default (L7) | as above | as above | **Alertmanager alert email + agent delivery** | Caution |
| `prod-rulepath-e2e` (L8) | as above | audit record + throwaway emitter container | whatever the marker route delivers | Yes, by design |
| `diag eval blind --live-url` (L6) | Loki | **pushes synthetic log lines into Loki** | per case | Caution |
| `smoke-test -RealPath` (L10) | — | **stops a container** | real pages | **No** |

Caveats that matter in practice:

- **LLM cost and latency.** L6/L7/L9 each cost one or more model calls per case.
  Use `--only` / `--limit` on production.
- **Notification channels.** Confirm where `severity=warning` routes before
  firing synthetic alerts, or you page an on-call for a smoke test. Prefer a
  dedicated marker route with `continue: false` that reaches the agent only.
- **Synthetic data in Loki.** A live blind eval pushes fabricated log lines into
  the production Loki tenant. They are labelled with the case service and expire
  with normal retention, but they *are* in your logs — prefer offline eval in
  production unless you are specifically testing retrieval.
- **Audit growth.** Every diagnosis appends to the audit JSONL. That is the
  intended evidence trail, but keep retention in mind on repeat runs.

### 4.2 Recommended production sequence

Run in this order and stop at the first failure — later layers assume earlier
ones pass.

```powershell
# 0. Read-only: is the deployment healthy and correctly wired?
curl.exe http://127.0.0.1:8001/health
ssh -i $HOME\.ssh\key.pem ec2-user@HOST "docker exec publishi-diagnostic-agent diag doctor"

# 1. Read-only: does the deployed workspace still validate?
ssh -i $HOME\.ssh\key.pem ec2-user@HOST `
  "docker exec publishi-diagnostic-agent sh -c 'diag validate && diag lint'"

# 2. Agent-only diagnosis path (one LLM call, no Alertmanager email)
.\scripts\smoke-test.ps1 -ContainerPrefix publishi -AgentUrl http://localhost:8001 -DirectAgent

# 3. Full reactive chain via the marker rule
.\scripts\prod-rulepath-e2e.ps1 -SshTarget ec2-user@HOST -IdentityFile $HOME\.ssh\key.pem -ContainerPrefix publishi

# 4. Scenario coverage (scope it — every scenario is an LLM call)
python scripts\runbook-e2e.py -w C:\path\to\host\workspace --mode live --scenario high-error-rate
```

Step 2 requires an HTTP path to the agent — an SSH tunnel is fine here, since
only `/health` and `/alert` are used:

```powershell
ssh -i $HOME\.ssh\key.pem -L 8001:127.0.0.1:8001 -L 3000:127.0.0.1:3000 ec2-user@HOST
```

Step 3 must **not** go through a tunnel alone: rule-path needs a Docker daemon on
the target host, which is exactly what `prod-rulepath-e2e` arranges over SSH.

### 4.3 Evidence to capture

A production test run should leave a record, not just a green console:

| Evidence | Where | What to check |
|---|---|---|
| `/health` snapshot | HTTP response | `status=ok`, `agent_initialized=true`, `redaction_rules>0`, `service_map=true`, expected `models` |
| Audit record | `/app/audit/diagnostics-<date>.jsonl` | New line for the test alert; expected `chat_provider` / `chat_model`; token counts present |
| Redaction | same audit line + `diag e2e` output | No tenant id, no tenant UUID, no host-specific secrets |
| Grafana annotation | Grafana annotations API, tag `diagnostic-agent` | Present when a token is configured |
| Delivery | Mailpit / Slack / PagerDuty | Exactly the channels you intended, and no others |
| Agent logs | `docker logs <agent>` | No `LLM structured output parse failed`, no client timeouts |

### 4.4 Interpreting a "passing but degraded" run

The API returns HTTP 200 for a diagnosis whose LLM output failed schema
validation, so **a green smoke test is not proof of a useful diagnosis**. Always
pair the exit code with content checks:

| Symptom | Meaning | Action |
|---|---|---|
| Report renders `Diagnosis unavailable: LLM did not return valid structured output` | Model returned an incomplete `Diagnosis`; repair + JSON retry also failed | Check `docker logs` for the validation errors; consider a stronger chat model |
| `redaction_rules=0` / `status=degraded` | Workspace not mounted where the agent expects | Fix the mount before trusting any other result |
| `service_map=false` | Profile thinner than intended; blast radius will be empty | Compare the deployed workspace with the intended one |
| Empty metrics snapshot in the report | PromQL does not match host metric names/labels | Fix `metrics_profile.yaml`, re-run L2 and L9 |
| `rag_used=false` unexpectedly | RAG disabled or corpus not indexed | Check `AGENT_RAG_ENABLED` and `rag_available` in `/health` |

---

## 5. Gates and cadence

| Gate | Layers | Where |
|---|---|---|
| **Every PR** (`ci.yml`) | L1 unit tests, L3 corpus lint, L2 validate on both bundled examples, profile health-check, Docker build | GitHub Actions |
| **Host PR** (recommended) | L2 + L3 against the host workspace via the pinned image | Host repo CI |
| **Pre-release** (`devel` → `main`) | L4 replay, L6 offline eval (with `--judge`), plus a DEV run of L7–L9 | Local / staging |
| **Post-deploy** | §4.2 sequence: `/health`, `diag doctor`, validate+lint, L7 `-DirectAgent`, L8 rule-path | Production |
| **After alerting config change** | L8 rule-path (that is what it guards) | DEV then production |
| **After model / prompt change** | L6 offline eval, compared against the previous result JSON | Local |
| **Periodic** (e.g. weekly) | L7 + L8 on production; L6 offline to catch model drift | Scheduled |

Neither L2 nor L3 needs credentials or a running stack, which is why they belong
on *every* pull request that touches a workspace — including host repos.

---

## 6. Pass criteria and triage

| Layer | Pass criteria |
|---|---|
| L1 | `pytest -q` exits 0 |
| L2 | `diag validate` exits 0; redaction rule count as expected |
| L3 | `diag lint` exits 0 |
| L4 | `pass_rate` 1.0 (any failure is a real routing regression) |
| L5 | Every probe reachable, or a deliberate, documented skip |
| L6 | No regression versus the previous run: `identified_accuracy`, `mean_keyword_recall`, `mean_judge_score`; `no-signal-control` still low-confidence |
| L7 | `Summary: N passed, 0 failed`, plus the §4.3 evidence |
| L8 | Remote `PASS: DiagnosticAgentSmokeMarker audit record written` |
| L9 | `e2e OK: <n> scenario(s)` with no redaction leak |
| L10 | New audit record appears while the dependency is down, and the service recovers afterwards |

Common failures and where to look:

| Failure | Likely cause |
|---|---|
| L8 times out with no audit record | Promtail not scraping, ruler group missing, or Alertmanager route not delivering to the agent webhook |
| L7 `/alert` times out | Prometheus/Loki unreachable from the agent (DNS), so retrieval hangs — run L5 |
| L9 redaction leak | Redaction rules do not cover the host's tenant identifiers |
| L6 high confidence, low accuracy | Overconfident model; a strong argument for keeping RAG on in production |
| L2 `redaction_rules=0` | Workspace not mounted, or `redaction.yaml` missing from the profile chain |

---

## 7. Ownership: agent repo vs host repo

| Owned here (diagnostic-agent) | Owned by the host |
|---|---|
| Unit tests, `diag` commands, `scripts/smoke-test`, `scripts/prod-rulepath-e2e`, `scripts/runbook-e2e` | Compose service names, ports, SSH targets |
| Blind eval harness and judge | Host workspace `scenarios.yaml`, `runbooks/`, redaction rules |
| This strategy and [TESTING.md](TESTING.md) | Loki ruler marker rule, Alertmanager routes |
| Result formats and pass criteria | Which environments run which gate, and on what schedule |

Hosts should keep **thin wrappers** that forward parameters (container prefix,
SSH target, `-w`) rather than copies of these scripts, so a fix here reaches
every host on the next image/checkout bump.

---

## 8. Known gaps

Deliberate limits of the current strategy — treat them as backlog, not as
covered ground:

- **No load or soak testing.** Concurrent alert storms, Loki query pressure, and
  LLM rate limits are untested.
- **No chaos beyond single-container stop** (L10). Partial failures such as a
  slow Loki or a throttled Bedrock endpoint are not simulated.
- **Execution path is only partially exercised.** L4 stops at the route
  decision; the sandbox and destructive classifier have unit coverage but no
  end-to-end production drill. See
  [docs/design/sandboxed-execution.md](design/sandboxed-execution.md).
- **Notification routes are usually skipped** on production runs to avoid paging
  on-call, so Slack/PagerDuty formatting is verified mainly by unit tests.
- **Eval sets are small.** Scores move meaningfully with a single case; treat
  them as trend indicators, not benchmarks.
