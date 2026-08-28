# Blind diagnostic eval

Measures how well the configured LLM identifies a root cause **from logs alone,
with no runbook/RAG context and no hints**. This isolates the model's reasoning
from the local knowledge base — the whole point of the exercise.

A second, much cheaper harness lives in this document too:
[routing replay](#routing-replay-eval-diag-replay) (`diag replay`) scores route
decisions with no LLM and no running stack.

---

## Topics

1. [What "blind" means here](#what-blind-means-here)
2. [How and from where the logs are injected](#how-and-from-where-the-logs-are-injected)
3. [Files](#files)
4. [CLI parameters](#cli-parameters-apptoolsblind_evalpy)
5. [Run it](#run-it)
6. [Metrics reported](#metrics-reported)
7. [Interpreting results](#interpreting-results)
8. [Routing replay eval (`diag replay`)](#routing-replay-eval-diag-replay)

---

## What "blind" means here

The live agent injects retrieved runbook text into the correlate prompt
(`Runbook / past-incident context: ...` in `app/graph/nodes.py`). This eval
forces that slot to `none`, so the model sees only:

- the alert labels (name, service, severity),
- an optional metrics snapshot,
- the injected log lines (formatted exactly like the live Loki pipeline).

It then scores the model's hypotheses against independent ground truth stored in
`blind_eval_dataset.yaml` (which never references the runbooks).

## How and from where the logs are injected

There are three log paths. The eval uses the middle and bottom rows; the top row
is how the agent works in production for context.

| Path | Log source | Injection point | Needs |
|---|---|---|---|
| Production | app stdout (logback JSON) | Promtail → Loki (agent *pulls*) | full stack |
| Eval **offline** (default) | `blind_eval_dataset.yaml` → `logs:` | straight into the prompt | LLM creds only |
| Eval **live** (`--live-url`) | `blind_eval_dataset.yaml` → `logs:` | Loki push API → agent *pulls* | agent + Loki running |

### Production (for reference)

The apps emit structured JSON to stdout (the `docker`/`kubernetes` logback
profile). **Promtail** scrapes container stdout and ships it to **Loki**, setting
the `service` label. The agent never receives logs directly — its `retrieve` node
*pulls* them:

```
platform-service / api-gateway ──stdout JSON──▶ Promtail ──▶ Loki {service="X"}
                                                                     ▲
diagnostic-agent (retrieve node) ── query_range {service="X"} | json | level=~"ERROR|WARN"
```

Source of the query: `app/graph/nodes.py` (`retrieve`) → `app/clients/loki.py`
(`query_range` + `format_log_entries`).

### Eval — offline mode (default)

**No Loki, no Promtail, no Prometheus.** The `logs:` list of each case in
`blind_eval_dataset.yaml` *is* the source. The runner:

1. `load_cases()` reads the YAML.
2. `format_logs()` runs the raw JSON lines through the **same**
   `LokiClient.format_log_entries()` the live pipeline uses, so they read
   identically to production (`[ts] [trace_id=..] Logger: msg`).
3. `build_prompt()` drops them into the `Recent error/warn logs (sample): ...`
   slot and hard-sets `Runbook / past-incident context: none`.

So the logs are injected **at the prompt boundary** — the model sees exactly the
lines you put in the dataset and nothing from the runbooks.

### Eval — live mode (`--live-url` + `--loki-url`)

Logs are injected **into Loki**, then the real agent pulls them exactly like
production. The dataset is still the source, but delivery is Loki's HTTP push API:

```
blind_eval_dataset.yaml ──push_logs_to_loki()──▶ Loki /loki/api/v1/push {service="X"}
                                                             │
POST /alert ──▶ diagnostic-agent (retrieve queries Loki) ◀──┘
```

`push_logs_to_loki()` POSTs a stream labeled `{"service": <case service>}` with
current-time nanosecond timestamps; `run_live()` then POSTs the alert and reads
the returned diagnosis. This is the same idea as the smoke test, except the smoke
test emits from a throwaway container that Promtail scrapes, whereas the eval
pushes straight to Loki (faster, no Promtail dependency).

> To keep a **live** run blind, start the agent with `AGENT_RAG_ENABLED=false`.
> Offline mode is always blind because it hard-codes the context to `none`.

## Files

| File | Purpose |
|---|---|
| `blind_eval_dataset.yaml` | Cases: injected logs + independent ground truth per system |
| `app/tools/blind_eval.py` | Runner (offline + live), scoring, optional LLM-as-judge |
| `results/` | Timestamped JSON result files (gitignored) |

## CLI parameters (`app/tools/blind_eval.py`)

Run from `diagnostic-agent/`. Print the same reference from the script:

```bash
diag eval blind -h
```

| Parameter | Type / default | Description |
|---|---|---|
| `--dataset PATH` | path; default *the workspace dataset* | YAML file with cases (`logs`, `expected`, alert labels). Use a custom path to trial a subset/fork of cases without editing the main dataset. |
| `--out DIR` | dir; default `<workspace>/eval-results/` | Where to write `blind-eval-<UTC-timestamp>.json` (summary + per-case diagnosis + scores). Created if missing. Gitignored. |
| `--only IDS` | string; default *all cases* | Comma-separated case `id` values from the dataset. Filters before `--limit`. Example ids: `jvm-heap-oom`, `postgres-connectivity`, `redis-connection`. |
| `--limit N` | int; default `0` (no limit) | After `--only` filtering, keep only the first **N** cases. Handy for a quick smoke of the harness. |
| `--judge` | flag; default off | Extra LLM call per case that grades the **full** diagnosis JSON (`issue_categories`, primary/secondary, next steps, etc.) against known root cause(s). Adds `judge_score` (0–5), `judge_correct`, `judge_reason`, and summary `mean_judge_score` / `judge_correct_rate`. Costs ~1× more LLM calls. |
| `--live-url URL` | string; default empty (= offline) | Base URL of a running diagnostic-agent. Enables **live** mode: `POST {URL}/alert` with the case’s alert labels and score the returned `diagnosis`. Does **not** inject logs by itself. Example: `http://localhost:8001`. |
| `--loki-url URL` | string; default empty | Loki base URL used **only in live mode** to inject the case’s `logs:` via `POST {URL}/loki/api/v1/push` before `/alert`. Example: `http://localhost:3100`. Ignored offline. If you set `--live-url` without `--loki-url`, no logs are pushed (agent sees whatever is already in Loki). |
| `--merge` | flag; default off | After `--only` / `--limit`, **combine** the remaining cases into **one** request: logs are round-robin interleaved then shuffled (`--merge-seed`), metrics deep-merged, alert becomes generic `HighErrorRate`. Scores per-system hit rate (a cause counts if its keywords appear in primary **or** secondary). Needs ≥2 cases. |
| `--merge-seed N` | int; default `42` | RNG seed for the shuffle step when `--merge` is set (reproducible mixed samples). |

### Mode selection

| Flags | Mode | What happens |
|---|---|---|
| *(neither)* | **Offline** | Build the correlate prompt in-process; RAG context = `none`; call the LLM directly. No Docker/Loki required. |
| `--live-url` only | **Live, no inject** | Fire `/alert`; agent pulls existing Loki logs for that service. |
| `--live-url` + `--loki-url` | **Live + inject** | Push case logs into Loki, wait ~3s, then fire `/alert`. |
| `--loki-url` without `--live-url` | *(ignored)* | `--loki-url` has no effect in offline mode. |

### Examples by parameter

**Default (all cases, offline, keyword scoring only):**

```bash
diag eval blind
```

**`--only` — single case or subset:**

```bash
diag eval blind --only jvm-heap-oom
diag eval blind --only jvm-heap-oom,redis-connection,postgres-connectivity
```

**`--limit` — first N cases (after any `--only` filter):**

```bash
diag eval blind --limit 3
diag eval blind --only postgres-connectivity,redis-connection,jvm-heap-oom --limit 1
# → runs only postgres-connectivity
```

**`--judge` — keyword scores + LLM-as-judge:**

Grades the **entire** diagnosis JSON (not primary alone). Causes listed only under
`issue_categories` still count. On `--merge`, required causes are listed per source
case; `insufficient-data` controls are excluded from the must-hit checklist.

```bash
diag eval blind --judge
diag eval blind --only jvm-heap-oom --judge
```

**`--dataset` / `--out` — custom dataset and result location:**

```bash
diag eval blind --dataset eval/blind_eval_dataset.yaml --out eval-results
diag eval blind --dataset /tmp/my-cases.yaml --out /tmp/blind-results
```

**`--live-url` / `--loki-url` — full pipeline for one case:**

```bash
# Start agent blind first (RAG off), then:
diag eval blind \
  --only jvm-heap-oom \
  --live-url http://localhost:8001 \
  --loki-url http://localhost:3100 \
  --judge
```

**`--merge` — mixed concurrent errors in one request (realistic Loki sample):**

Default blind eval runs **one case per request**, so the model never has to
disentangle overlapping failures. Production samples are mixed. `--merge`
interleaves the selected cases into a single offline prompt or live `/alert`:

```bash
# Offline mixed: postgres + redis + JVM in one diagnosis
diag eval blind \
  --merge --only postgres-connectivity,redis-connection,jvm-heap-oom \
  --judge

# Live mixed (push interleaved logs, one HighErrorRate alert)
diag eval blind \
  --merge --only postgres-connectivity,redis-connection,openai-rate-limit \
  --live-url http://localhost:8001 --loki-url http://localhost:3100 \
  --judge

# Reproducible shuffle
diag eval blind --merge --merge-seed 7 --limit 4
```

Scores report `systems_hit / systems_total` and a `per_system` breakdown (a
system counts as a hit when its `cause_keywords` appear anywhere in the
diagnosis text pool, not only the primary hypothesis).

**PowerShell equivalents:**

```powershell
diag eval blind --only jvm-heap-oom --judge
diag eval blind --live-url http://localhost:8001 --loki-url http://localhost:3100 --only redis-connection
```

### Related environment variables (not CLI flags)

These affect the LLM the runner uses (same `AGENT_*` settings as the agent). They are **not** argparse flags; set them in the shell or `.env`.

| Variable | Purpose | Example |
|---|---|---|
| `AGENT_CHAT_PROVIDER` | Chat backend | `openai`, `bedrock_converse`, `ollama` |
| `AGENT_CHAT_MODEL` | Model id | `gpt-4o-mini`, `amazon.nova-micro-v1:0` |
| `AGENT_LLM_TEMPERATURE` | Sampling temperature (default `0.1`) | `0.1` |
| `OPENAI_API_KEY` | OpenAI credentials (when provider is openai) | `sk-...` |
| `AWS_REGION` / `DIAGNOSTIC_AGENT_AWS_*` | Bedrock credentials (compose maps these into the agent; for offline eval use standard AWS env/chain) | — |
| `DIAGNOSTIC_AGENT_RAG_ENABLED` | Live agent only: set `false` so the running container stays blind | `false` |

```bash
AGENT_CHAT_PROVIDER=openai AGENT_CHAT_MODEL=gpt-4o-mini OPENAI_API_KEY=sk-... \
  diag eval blind --only jvm-heap-oom --judge
```

```powershell
$env:AGENT_CHAT_PROVIDER="openai"; $env:AGENT_CHAT_MODEL="gpt-4o-mini"; $env:OPENAI_API_KEY="sk-..."
diag eval blind --only jvm-heap-oom --judge
```

## Run it

All commands run from the `diagnostic-agent/` directory. The offline runner
reuses the **real** system prompt, model factory, output schema, and Loki log
formatting, so results closely match production minus RAG. It uses whatever
`AGENT_CHAT_*` provider is configured (Bedrock / OpenAI / Ollama) and reads
credentials from the standard SDK env vars (`OPENAI_API_KEY`, AWS chain, …).

### Offline (default — fastest, no stack)

```bash
cd diagnostic-agent

# 1) Full run, all cases
diag eval blind

# 2) Add a semantic 0-5 LLM-as-judge score vs the ground-truth root cause
diag eval blind --judge

# 3) Only specific cases (comma-separated ids from blind_eval_dataset.yaml)
diag eval blind --only postgres-connectivity,redis-connection

# 4) Quick sanity run of the first 3 cases
diag eval blind --limit 3

# 5) Point at an alternate dataset / output dir
diag eval blind --dataset eval/blind_eval_dataset.yaml --out eval-results
```

PowerShell (Windows) is identical, e.g.:

```powershell
cd diagnostic-agent
diag eval blind --judge
```

Force a specific provider for one run without editing `.env`:

```bash
# OpenAI
AGENT_CHAT_PROVIDER=openai AGENT_CHAT_MODEL=gpt-4o-mini OPENAI_API_KEY=sk-... \
  diag eval blind --judge
```

```powershell
# OpenAI (PowerShell)
$env:AGENT_CHAT_PROVIDER="openai"; $env:AGENT_CHAT_MODEL="gpt-4o-mini"; $env:OPENAI_API_KEY="sk-..."
diag eval blind --judge
```

### Live (full pipeline: dataset → Loki → agent)

Start the agent **blind** (RAG off) and let the runner push logs into Loki before
firing each alert:

```bash
# start the agent with RAG disabled (compose maps DIAGNOSTIC_AGENT_* -> AGENT_*)
DIAGNOSTIC_AGENT_RAG_ENABLED=false docker compose -f docker-compose.yml \
  -f docker-compose.observability.yml --profile diagnostic-agent up -d --force-recreate diagnostic-agent

cd diagnostic-agent
diag eval blind --live-url http://localhost:8001 --loki-url http://localhost:3100
```

The live path records `_rag_used` on each diagnosis so you can confirm the run
was truly blind (`false`). If you omit `--loki-url`, the runner assumes the logs
are already in Loki and only POSTs the alerts.

### Example output

```
Blind eval: 11 cases | mode=offline | judge=on

  [OK ] postgres-connectivity        system=postgresql            recall=1.0  conf=high judge=5
  [OK ] db-pool-exhaustion           system=database-pool         recall=1.0  conf=high judge=5
  [MISS] gateway-upstream-timeout    system=web-server-gateway    recall=0.33 conf=medium judge=2
  ...
  [OK ] no-signal-control            system=insufficient-data     recall=0.2  conf=low  judge=5

Summary:
  cases: 11
  scored: 11
  identified_accuracy: 0.818
  mean_keyword_recall: 0.74
  mean_judge_score: 4.1
  judge_correct_rate: 0.818

Wrote eval-results/blind-eval-20260721T0530Z.json
```

Each run also writes a full JSON file (per-case diagnosis + **`llm_exchange`**
with system/user prompts and token usage + **`llm_context`** for RAG + score +
aggregate) to `eval-results/` for later comparison. That folder is gitignored.
Offline runs always have `rag_used=false` and empty `rag_context` under
`llm_context`; live runs mirror the agent's `report.llm_context` /
`report.llm_exchange` (confirm RAG-off via `llm_context.rag_used`).

### Measuring how much the runbooks help (RAG on vs off)

Run the eval against a RAG-**off** agent, then a RAG-**on** agent, and compare the
`identified_accuracy` / `mean_judge_score` in the two result files:

```bash
# blind (RAG off) — as above
diag eval blind --live-url http://localhost:8001 --loki-url http://localhost:3100

# restart the agent with RAG enabled, then re-run
DIAGNOSTIC_AGENT_RAG_ENABLED=true docker compose -f docker-compose.yml \
  -f docker-compose.observability.yml --profile diagnostic-agent up -d --force-recreate diagnostic-agent
diag eval blind --live-url http://localhost:8001 --loki-url http://localhost:3100
```

The delta quantifies the runbooks' contribution (and confirms `_rag_used` flips
from `false` to `true`).

## Metrics reported

- `models` — chat/embed provider + model ids used for diagnosis (and `judge`
  when `--judge` is on). Live diagnosis models come from the agent `/health`
  (or `llm_exchange`); offline/judge use host `AGENT_*` settings.
- `identified_accuracy` — primary hypothesis named the right system/cause
  (for `--merge`: all source systems hit in the diagnosis text pool).
- `mean_systems_hit_rate` — with `--merge`: fraction of concurrent causes found.
- `mean_keyword_recall` — coverage of expected cause keywords across the diagnosis.
- `grounded` (per case) — evidence cited provided log tokens (anti-hallucination).
- `confidence_note` — the model's self-reported confidence (calibration).
- `mean_judge_score` / `judge_correct_rate` — with `--judge`.

## Interpreting results

- High `identified_accuracy` + high `grounded` = the model reasons well from raw
  logs without the runbooks.
- Low accuracy with **high** confidence = overconfident / hallucinating; a strong
  argument for keeping RAG enabled in production.
- The `no-signal-control` case should come back **low** confidence — if the model
  invents a cause there, it is not calibrated.

Compare a RAG-off run (this eval) against a RAG-on run (point `--live-url` at an
agent started with RAG enabled) to quantify how much the runbooks actually help.

---

# Routing replay eval (`diag replay`)

Where the blind eval measures the *model*, replay measures the *routing*: given
an alert's labels and a simulated confidence, does
[`should_route`](../app/graph/routing.py) send it to `report`, `escalate`, or
`execute`? It calls no LLM, no Prometheus, no Loki, and no sandbox, so it runs
in CI on every push and is the guard you want before enabling routing — let
alone execution — on a real host.

```bash
diag replay                                   # every scenario in the workspace
diag replay --only redis-connection-errors    # one case
diag replay --dataset ./scenarios.yaml --out ./replay-results
```

| Parameter | Default | Description |
|---|---|---|
| `--dataset PATH` | workspace `scenarios.yaml` | Replay cases. Reads a top-level `scenarios:` (or `cases:`) list |
| `--out DIR` | `<workspace>/replay-results/` | Where `replay-eval-<UTC-timestamp>.json` is written |
| `--only IDS` | *all cases* | Comma-separated scenario `id` values; unknown ids print a warning |

## What a case looks like

Replay reuses the scenarios you already keep for `diag lint` / `diag e2e`. The
optional `replay:` block pins the expectation; see
[WORKSPACE.md](../docs/WORKSPACE.md#scenariosyaml).

```yaml
scenarios:
  - id: redis-connection-errors
    runbook: runbook-redis-connection-errors.md
    labels:
      alertname: RedisConnectionErrors
      service: platform-service
      severity: warning
    replay:
      expected_route: execute
      expected_runbook: runbook-redis-connection-errors.md
      confidence_note: high
```

Defaults when `replay:` is absent or partial:

| Field | Default |
|---|---|
| `expected_route` | `escalate` when `labels.severity` is `critical`, else `report` |
| `expected_runbook` | the case's `runbook` when the expected route is `execute`, else none |
| `confidence_note` | `high` when the expected route is `execute`, else `medium` |

## How it runs

Each case is turned into a minimal state — severity, normalized severity, the
simulated `confidence_note`, and a stand-in `rag_context` naming the selected
runbook — and passed through the real `should_route`. Routing is **forced on**
for the duration of the run and restored afterwards, so results do not depend on
`AGENT_ROUTING_ENABLED`. A case passes when both the route and the selected
runbook match the expectation.

```
ok   redis-connection-errors: route=execute runbook=runbook-redis-connection-errors.md
FAIL db-pool-exhaustion: route=report expected=execute; runbook=(none) expected=runbook-db-pool-exhaustion.md

Summary:
  cases: 12
  passed: 11
  failed: 1
  pass_rate: 0.917
```

The same summary plus per-case rows is written to
`<workspace>/replay-results/replay-eval-<timestamp>.json`, and the command exits
`1` if any case fails.

## What it does not cover

Replay stops at the route decision. It does not exercise `execute_runbook`,
the destructive classifier, the sandbox, or delivery, and it does not read
`AGENT_EXEC_ENABLED`. A green replay run means alerts are routed as intended —
not that an action would be safe to run. For that, see
[docs/design/sandboxed-execution.md](../docs/design/sandboxed-execution.md).
