# Install operator prompt (LLM-assisted onboarding)

Use this when an LLM (Cursor agent, ChatGPT, etc.) should install and configure
**diagnostic-agent against an existing stack** — Docker services, Prometheus,
Loki, Alertmanager, and your app — **without inventing topology or secrets**.

Related: [INSTALL.md](INSTALL.md) · [WORKSPACE.md](WORKSPACE.md) ·
[INTEGRATING.md](INTEGRATING.md)

---

## Topics

1. [When to use this](#when-to-use-this)
2. [How to use the prompt](#how-to-use-the-prompt)
3. [Quality bar (required depth)](#quality-bar-required-depth)
4. [Copy-paste prompt](#copy-paste-prompt)
5. [Clarification checklist](#clarification-checklist)
6. [Recommended loop](#recommended-loop)
7. [Success criteria](#success-criteria)

---

## When to use this

| Good fit | Poor fit |
|---|---|
| You already have a running observability + app stack | Greenfield "invent my architecture" |
| You want install + workspace tuning with a human in the loop | Fully unattended production mutation |
| You will answer preset / labels / secrets questions | Silent guessing of tenant redaction or AM routes |

Install **discovers wiring**; the operator (you + LLM) still owns **host
semantics** (`service_map`, preset, redaction, which alerts hit the agent).

The install tree is **self-sufficient**: profiles, full runbooks, scenarios, and
`blind_eval.yaml` land under `<output>/agent/workspace/` so validate / lint /
eval need no separate host monorepo path.

**Leaving installer stubs untouched is not a successful install.** The agent must
rewrite workspace files to match *this* stack with the depth in
[Quality bar](#quality-bar-required-depth).

---

## How to use the prompt

1. Open a chat in the **diagnostic-agent** repo (and optionally the host repo,
   e.g. publishi.ai, if that is where compose/live services live).
2. Paste the [Copy-paste prompt](#copy-paste-prompt) as the system/user brief.
3. Fill the **Context block** at the bottom (output dir, host paths, constraints).
4. Let the LLM propose commands and file edits; answer its clarification
   questions before it writes secrets or merges Prom/AM config.

On Windows, if `diag` is missing from PATH, use `python -m app.cli …` — see
[Putting `diag` on PATH](INSTALL.md#putting-diag-on-path).

---

## Quality bar (required depth)

A finished install must match the **depth and evidence discipline** of a
reference host bundle such as `deploy/opus5/publishi/` (publishi.ai). That tree
is an *example of quality*, not a topology to copy onto unrelated stacks.

Installer stubs alone (thin `extends:` + 3–4 service map nodes) are **incomplete**.

### What "opus5-quality" looks like

| Artifact | Stub / shallow (reject) | Required depth |
|---|---|---|
| `service_map.yaml` | 3–4 nodes, one-line descriptions, empty `module_dependencies` | Every alert `service=` label + every Loki stream + backing deps + logical targets (`security`, `frontend`, `host`, `container`, …). `log_services` / `log_selector` where logs are not under the alert label. `modules` + `module_dependencies` for monoliths. Descriptions name real containers, ports, and roles. Header cites the host rule files that define the labels. |
| `metrics_profile.yaml` | `extends: <preset>` only | Live-verified PromQL. Override any preset template that returns **0 series** (e.g. missing histogram buckets → mean/`_max` latency). Pin client-side probes (HikariCP, Lettuce) to the **app** `service=` label, not the store name. Document gaps in comments. |
| `logs_profile.yaml` | stub `extends` | Real `service_label`, JSON parser, `module_regex` matched to a **sample log line**, and `alert_line_filters` for **every** host `alertname` (Prom + Loki rulers), patterns aligned with ruler regexes. |
| `prompt_profile.yaml` | stub | Architecture narrative (request path, modules, backing stores, observability). Explicit **measurement gaps** so the LLM does not treat empty series as healthy. `tool_run_hints` with copy-pasteable curls/`docker exec` against **this** stack's hostnames. |
| `redaction.yaml` | stub `extends` only | Host-specific tenant/PII/credential rules on top of the preset (tenant ids, JWT, emails, DB URIs, cloud keys, …) grounded in labels/fields the stack actually emits. |
| `scenarios.yaml` | catalog defaults with wrong `service=` | One scenario per diagnosable alert the host fires; labels match live Alertmanager/Loki rulers; `rag_must_contain` grounded. |
| `runbooks/` | catalog subset only | Keep full corpus; trim only with operator approval. Prefer runbooks that intersect host alertnames. |
| `docker-compose.yml` / `.env` | stock ports/names | Unique `container_name` + host port if 8001 taken; join real Docker network; pin `AGENT_PROFILE_DIR` / `AGENT_RUNBOOKS_PATH`; Bedrock ambient AWS mount only if approved; webhook `/alert`. |
| `APPLY.md` | generic checklist | Per-step status against **this** stack (DONE / DO NOT MERGE / ALREADY WIRED). Document host alert/AM paths. List host-stack gaps found during evidence checks (do not silently "fix" production). |

### Evidence the agent must collect before rewriting workspace files

Run (or equivalent) and **cite results** in file headers / APPLY.md:

```bash
docker ps --format "{{.Names}}\t{{.Image}}\t{{.Ports}}"
docker network ls
# Prometheus: metric names + label values
curl -sG http://127.0.0.1:9090/api/v1/label/__name__/values
curl -sG http://127.0.0.1:9090/api/v1/label/service/values
curl -sG http://127.0.0.1:9090/api/v1/query --data-urlencode \
  'query=count(http_server_requests_seconds_bucket)'
# Loki
curl -sG http://127.0.0.1:3100/loki/api/v1/label/service/values
# Sample a real log line (adjust service=); check logger_name shape
# Host alert / AM / promtail sources (from container mounts or host repo)
```

Probe every PromQL template you keep or add: **0 series ⇒ override or remove**.
Probe dependency probes with the **alerting** service name substituted — if empty,
pin to the client app label.

### Minimum acceptance gates (before declaring done)

- [ ] `diag validate && diag lint` green on the generated workspace (published image mount)
- [ ] `GET /health` → `ok`, expected preset, `redaction_rules` ≫ preset-only count
- [ ] `service_map` service count matches real alert/Loki labels (not just gateway+app+db)
- [ ] At least one live `POST /alert` (or eval) returns evidence using **this** stack's
      metric names / LogQL / `docker exec` hostnames — not empty PromQL
- [ ] `APPLY.md` records what was merged vs left as snippets-only, and host gaps found

---

## Copy-paste prompt

```text
You are an install operator for diagnostic-agent against an EXISTING stack
(apps + Docker services + Prometheus / Loki / Alertmanager / optional Grafana).

## Goals
1. Read and follow: docs/INSTALL.md, docs/WORKSPACE.md, docs/INTEGRATING.md,
   docs/INSTALL_OPERATOR.md (especially "Quality bar"), and eval/README.md only
   if running blind eval.
2. Discover the running system with evidence (docker ps / compose, networks,
   published ports, Prom/Loki/AM URLs, live metric names, Loki service= values,
   sample log lines, host alert-rule and Alertmanager files) — do not invent
   services.
3. Run or re-run `diag install` (or `python -m app.cli install`) into the output
   directory I specify.
4. REWRITE the generated workspace to opus5-quality depth for THIS stack
   (see docs/INSTALL_OPERATOR.md § Quality bar). Installer stubs are a starting
   point, not the deliverable. Touch at least: service_map.yaml,
   metrics_profile.yaml, logs_profile.yaml, prompt_profile.yaml, redaction.yaml,
   scenarios.yaml; tune compose/.env/APPLY.md for placement and host wiring.
5. When anything is ambiguous, ASK a clear clarification question and WAIT.
   Do not assume.

## Quality bar (non-negotiable)
- Match the depth of deploy/opus5/publishi/ as a quality example: full topology
  (edge + apps + backing stores + logical alert targets), evidence-backed PromQL
  overrides, alert_line_filters for every host alertname, host-specific
  redaction, rich prompt_profile with measurement gaps + tool_run_hints, scenarios
  aligned to live labels, APPLY.md with per-step status and host gaps.
- Do NOT stop at `extends: <preset>` stubs or a 3-node service_map.
- Verify every kept PromQL template against live Prometheus (0 series = fix).
- Client-side pool/cache metrics (HikariCP, Lettuce, …) must query the APP
  service label, not postgres/redis.
- module_regex must match a real logger_name from a sampled log line.
- Headers/comments in workspace files must cite evidence (which Prom/Loki/host
  files you read).

## Hard rules
- Prefer evidence (command output, file contents, install-report.json, APPLY.md)
  over guesses.
- Fail closed: do not weaken redaction; do not set AGENT_REQUIRE_REDACTION=false
  unless I explicitly allow it.
- Do not enable Bedrock/OpenAI/email/Grafana annotations without my OK for the
  provider and where credentials come from.
- Do not merge Prometheus/Alertmanager config into production files unless I
  explicitly approve; generating snippets under observability/ is fine. If the
  host already has richer rules/routes, mark APPLY.md "DO NOT MERGE" / "ALREADY
  WIRED" instead of duplicating.
- Alertmanager webhook path must be /alert (not /webhook).
- Do not commit secrets (.env). Warn if I ask you to.
- One coherent change set at a time; summarize files touched after each step.
- If another agent uses port 8001 / name diagnostic-agent, pick a distinct host
  port and container_name (compose pins project name from the output directory).

## What you may automate
- Running install / dry-run / validate / lint / a test POST /alert
- Reading APPLY.md + install-report.json
- Inspecting docker/compose/Prom/Loki/host rule files
- Starting from spring-micrometer example seeding or generic stubs, then
  rewriting them from live evidence
- Proposing Mailpit SMTP and container-DNS URLs when discovery supports them
- Pinning AGENT_PROFILE_DIR=/workspace and AWS ambient mounts when approved

## What you must clarify before acting
- Preset: generic-prometheus vs spring-micrometer (confirm against live metrics)
- Canonical service= / alert service label names and topology edges
- Which alerts should route to the agent vs stay human-only
- Tenant/PII redaction rules beyond the preset
- LLM provider + how credentials are supplied (explicit keys vs ambient AWS)
- Grafana token / annotations on or off
- SMTP vs Mailpit (and container-reachable hostname)
- Network placement (same Docker network vs host.docker.internal)
- Whether to apply Prom/AM reloads automatically
- Host port / container name if 8001 or diagnostic-agent is already taken

## Deliverables after each phase
- Commands run + short evidence summary (include series counts / label values)
- Files changed (paths only; no secret values)
- Open questions (numbered)
- validate / lint / health results when applicable
- Remaining gaps (host defects found vs bundle still incomplete)

## Done means
- Quality-bar checklist in docs/INSTALL_OPERATOR.md is satisfied
- diag validate && diag lint pass on the workspace
- /health ok with preset + redaction_rules > 0
- At least one test alert/diagnosis uses this stack's names (not empty PromQL)

## Context (fill in before starting)
- Install output directory: <e.g. ./deploy/opus2/publishi>
- Quality reference (read-only example): deploy/opus5/publishi
- Host / compose root (if any): <path or "same machine, docker only">
- Known constraints: <e.g. do not touch production AM yet; use Ollama; …>
- Preferred preset if known: <spring-micrometer | generic-prometheus | ask me>
```

---

## Clarification checklist

Use this as a quick gate before the LLM writes workspace files:

- [ ] Preset matches exported metrics (Spring Micrometer vs community HTTP)
- [ ] `service_map.yaml` names match Loki `service=` and alert labels
- [ ] `AGENT_DEFAULT_PRESET` equals `agent.yaml` `extends`
- [ ] Prometheus / Loki / webhook URLs reachable **from the agent container**
- [ ] Webhook path ends with `/alert`
- [ ] Redaction rules present (`diag validate` / `/health` redaction_rules > 0)
- [ ] LLM credentials strategy agreed (Bedrock → Titan embed + region kwargs)
- [ ] Email / Grafana optional channels explicitly on or off
- [ ] Prom/AM merge plan approved (snippets only vs apply+reload)
- [ ] Compose project `name:` left intact; port/container unique if needed
- [ ] Quality bar depth agreed (full rewrite vs stubs-only is **not** OK)

Post-rewrite gate (before "done"):

- [ ] Every `service_map` alert label / Loki stream represented
- [ ] PromQL templates live-probed; empty ones overridden
- [ ] `alert_line_filters` cover host alertnames
- [ ] `prompt_profile` includes measurement gaps + host-specific tool hints
- [ ] `APPLY.md` has per-step status + host gaps
- [ ] Test `POST /alert` (or eval) shows non-empty stack-specific evidence

---

## Recommended loop

1. **Discover** — docker/compose, ports, networks, Prom/Loki/AM; sample metric
   names, `service=` values, one log line, host alert + AM + promtail sources.
2. **Clarify** — preset, service names, secrets, apply scope, port conflicts.
3. **Install** — `diag install --output <dir>` (or `python -m app.cli install …`).
4. **Rewrite workspace to quality bar** — do not ship stubs. Evidence-backed
   `service_map`, metrics/logs/prompt/redaction/scenarios; tune compose/.env;
   rewrite `APPLY.md` with DONE / DO NOT MERGE / host gaps.
5. **Validate** — mount workspace into the published image:

   ```bash
   docker run --rm -v "$PWD/<output>/agent/workspace:/workspace:ro" \
     ghcr.io/mskrado/diagnostic-agent:latest \
     sh -c "diag validate && diag lint"
   ```

   See [Validate with the published image](WORKSPACE.md#validate-with-the-published-image).

6. **Start + health** — `docker compose --env-file .env up -d` then `curl /health`.
   First start may stay `unhealthy` for a few minutes while RAG embeds the
   runbook corpus.
7. **Prove diagnosis** — one test `POST /alert` (or blind eval) and confirm
   evidence uses this stack's PromQL/LogQL/hostnames:

   ```bash
   curl -X POST http://127.0.0.1:<port>/alert -H 'Content-Type: application/json' \
     -d '{"alerts":[{"status":"firing","labels":{"alertname":"<host-alert>","service":"<host-service>","severity":"warning"}}]}'

   diag eval blind -w ./<output>/agent/workspace --limit 3
   ```

---

## Success criteria

The agent is "successfully monitoring" the underlying system when:

1. `GET /health` is `ok` (not degraded) with expected preset and redaction_rules > 0.
2. `service_map` reflects real services (blast radius non-empty for known apps),
   including backing stores and logical alert targets — not a stub 3-node map.
3. A test alert (or live eval case) returns a diagnosis that uses **this stack's**
   metric/log naming — not empty PromQL / wrong `service=` streams.
4. Workspace depth matches the [Quality bar](#quality-bar-required-depth)
   (opus5-level evidence and coverage).
5. Optional channels (email, Grafana) behave as you configured (on or intentionally off).
