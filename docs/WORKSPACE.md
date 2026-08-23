# Host workspace reference

A **workspace** is one directory in your repository holding everything specific
to your stack. The agent ships as a generic image; the workspace is the only
thing you write.

```
infrastructure/diagnostic-agent/
├── agent.yaml            # manifest (preset, paths, optional version pin)
├── metrics_profile.yaml  # PromQL templates (or profile/ subdirectory)
├── logs_profile.yaml     # Loki labels + alert line filters
├── prompt_profile.yaml   # platform description + tool-run hints for the LLM
├── redaction.yaml        # tenant / PII scrubbing (fail-closed)
├── service_map.yaml      # topology / blast radius
├── execution_profile.yaml # optional allowlist for sandboxed runbook actions
├── scenarios.yaml        # alert → runbook pairs for lint / e2e / replay
├── blind_eval.yaml       # optional synthetic cases for `diag eval blind`
└── runbooks/             # markdown playbooks for RAG
```

`diag install` writes the same layout under `deploy/agent/workspace/` as editable
stubs. Full install-bundle files (`.env`, compose, observability snippets) are
documented in [INSTALL.md](INSTALL.md#what-gets-generated).

Because the manifest declares every path, commands take no path arguments:

```bash
docker run --rm -v "$PWD/infrastructure/diagnostic-agent:/workspace:ro" \
  ghcr.io/mskrado/diagnostic-agent:<tag> diag validate
```

---

## Topics

1. [Locating the workspace](#locating-the-workspace)
2. [How the agent uses the workspace](#how-the-agent-uses-the-workspace)
3. [Manifest (`agent.yaml`)](#manifest-agentyaml)
4. [Flat layout](#flat-layout)
5. [File-by-file reference](#file-by-file-reference)
   - [`metrics_profile.yaml`](#metrics_profileyaml)
   - [`logs_profile.yaml`](#logs_profileyaml)
   - [`prompt_profile.yaml`](#prompt_profileyaml)
   - [`redaction.yaml`](#redactionyaml)
   - [`service_map.yaml`](#service_mapyaml)
   - [`execution_profile.yaml`](#execution_profileyaml-optional)
   - [`scenarios.yaml`](#scenariosyaml)
   - [`blind_eval.yaml`](#blind_evalyaml-optional)
   - [`runbooks/`](#runbooks)
6. [Precedence](#precedence)
7. [Redaction is fail-closed](#redaction-is-fail-closed)
8. [Validating in CI](#validating-in-ci)

---

## Locating the workspace

Resolved in this order:

1. `-w` / `--workspace` on the command line
2. `AGENT_WORKSPACE` (the image sets this to `/workspace`)
3. The nearest enclosing directory containing `agent.yaml`
4. The working directory

## How the agent uses the workspace

At process start the agent:

1. Resolves the workspace directory.
2. Loads `agent.yaml` (optional) for `extends`, path overrides, and version pin.
3. Loads the **integration profile** (flat files in the workspace root, or
   `profile/`) and merges each section through its `extends:` chain onto a
   built-in preset.
4. Builds the RAG index from `runbooks/` when `AGENT_RAG_ENABLED=true`.
5. Applies `redaction.yaml` to every outbound report / email / annotation.

During a diagnosis (webhook or `POST /alert`):

| Input | Workspace piece consulted |
|---|---|
| Alert `service=` / related services | `service_map.yaml` → blast radius + extra probes |
| Prometheus queries | `metrics_profile.yaml` templates for that `kind` |
| Loki queries | `logs_profile.yaml` labels + optional `alert_line_filters` |
| LLM system framing | `prompt_profile.yaml` (`platform_description`, `tool_run_hints`) |
| Retrieved playbooks | `runbooks/` via RAG (if enabled) |
| Scrubbing before delivery | `redaction.yaml` |

Wrong preset or an empty/stub `service_map.yaml` does not crash the agent, but
live scoring against a real stack (metrics names, topology, hints) will be weak.

## Manifest (`agent.yaml`)

`agent.yaml` is optional. A directory following the conventional layout resolves
identically; the manifest exists to pin a version, choose a preset, and override
paths.

| Key | Default | Purpose |
|---|---|---|
| `schema` | `1` | Manifest format. The agent refuses a schema newer than it supports. |
| `agent_version` | *(none)* | Version this workspace was written against. `diag validate` warns on a mismatch. |
| `extends` | `generic-prometheus` | Built-in preset the profile inherits from. **Must match** the metric naming your apps export. |
| `profile` | `./profile`, else the workspace root | Integration profile directory. Install uses `profile: .` (flat layout). |
| `runbooks` | `./runbooks` | RAG corpus. |
| `scenarios` | `./scenarios.yaml` | Runbook E2E scenarios. |
| `blind_eval` | `./blind_eval.yaml` | Blind-eval dataset (optional; pass `--dataset` if absent). |

```yaml
schema: 1
agent_version: 0.1.0
extends: spring-micrometer
profile: .
runbooks: ./runbooks
scenarios: ./scenarios.yaml
blind_eval: ./blind_eval.yaml
```

Unknown keys are ignored with a warning, so a typo surfaces in `diag validate`
rather than silently doing nothing. A path you *declare* must exist — that is an
error, not a fallback — while a path you omit is simply absent, and tools skip
the checks that need it. This lets you adopt the corpus incrementally.

### Presets (`extends`)

Presets ship **inside the image** under `app/profile/presets/`. Workspace files
normally only declare `extends: <preset>` and add host-specific overlays.

| Preset | Use when apps expose |
|---|---|
| `generic-prometheus` | Community `http_requests_total` / classic exporter naming |
| `spring-micrometer` | Spring Boot Actuator / Micrometer (`http_server_requests_seconds_*`, HikariCP, JVM) |

Every preset chain is rooted at `generic-prometheus`, so a partial overlay can
never resolve a section to nothing. Presets carry naming conventions, **not**
topology — `service_map.yaml` always comes from your profile.

Also set `AGENT_DEFAULT_PRESET` in the agent `.env` to the same value so runtime
and workspace stay aligned.

## Flat layout

Profile sections sitting directly in the workspace root are detected as the
profile, so a small host needs no `profile/` subdirectory:

```
diagnostic-agent/
├── agent.yaml
├── metrics_profile.yaml
├── logs_profile.yaml
├── redaction.yaml
├── prompt_profile.yaml
├── service_map.yaml
└── runbooks/
```

Both bundled examples use this layout — see
[`examples/hello-world/`](../examples/hello-world/) and
[`examples/spring-modular-monolith/`](../examples/spring-modular-monolith/).

---

## File-by-file reference

### `metrics_profile.yaml`

**Purpose.** PromQL templates the agent substitutes with `{service}` / `{window}`
(and related placeholders) when probing error rate, latency, saturation, and
dependency health.

**How it is used.** After an alert arrives, the agent selects templates by the
alerting service’s `kind` in `service_map.yaml` (and preset defaults). Results
become evidence in the hypothesis report.

**How to configure.**

1. Start with `extends: spring-micrometer` or `extends: generic-prometheus`
   matching your apps.
2. Only override `templates:` entries you need to customize.
3. Keep label names consistent with what Prometheus actually scrapes.

```yaml
extends: spring-micrometer
# Optional overrides:
# templates:
#   error_rate: >-
#     sum(rate(http_server_requests_seconds_count{...}[{window}])) / ...
```

Install writes a one-line stub. Prefer copying
`examples/spring-modular-monolith/metrics_profile.yaml` for Spring hosts.

### `logs_profile.yaml`

**Purpose.** Controls how the agent queries Loki: which label is the service
key, default level gate, optional JSON parsing, module extraction, and
per-alertname line filters.

**How it is used.** During `retrieve`, the agent builds LogQL from this profile.
If `alert_line_filters` has an entry for the firing `alertname`, that regex is
applied and the level gate may be relaxed so INFO lines that still match the
ruler are included.

**How to configure.**

| Field | Meaning |
|---|---|
| `service_label` | Loki label for the app name (almost always `service`) |
| `level_filter` | Default severity gate, e.g. `ERROR\|WARN` |
| `use_json_parser` | Prefer JSON log fields when streams are structured |
| `module_regex` | Capture group for package/module hints (Spring: `c\.p\.([a-z]+)`) |
| `alert_line_filters` | Map `alertname` → regex for targeted retrieval |

```yaml
extends: generic-prometheus
service_label: service
level_filter: "ERROR|WARN"
use_json_parser: true
module_regex: 'c\.p\.([a-z]+)'
alert_line_filters:
  PostgresErrorsInLogs: "(?i)(postgres|jdbc|hikari).*(refused|timeout)"
```

### `prompt_profile.yaml`

**Purpose.** Host-specific framing injected into the LLM prompt:
`platform_description` and `tool_run_hints`.

**How it is used.** The model sees your stack vocabulary and suggested
investigation commands. Core safety rules (hypotheses-only, evidence grounding,
JSON schema) stay in agent code and **cannot** be overridden here.

**How to configure.**

1. Describe the real architecture in `platform_description` (gateway, monolith,
   backing stores).
2. Put copy-pasteable curl / docker / actuator examples in `tool_run_hints`
   using **your** hostnames and ports.
3. Keep hints honest: suggested human steps, not claims the agent already ran
   them.

### `redaction.yaml`

**Purpose.** Regex rules that scrub secrets and tenant/PII from reports, email,
annotations, and audit-adjacent surfaces.

**How it is used.** Rules **accumulate** along the `extends:` chain — your rules
are appended to the preset’s secret scrubbing. Reuse a parent rule’s `name` to
override it. Startup is **fail-closed**: zero resolved rules → agent refuses to
start (unless `AGENT_REQUIRE_REDACTION=false`).

**How to configure.**

```yaml
extends: generic-prometheus   # keeps bearer_token + aws_access_key from preset
rules:
  - name: tenant_kv
    pattern: '("?tenant[_-]?id"?\s*[:=]\s*")[^"]*(")'
    replacement: '\1[REDACTED]\2'
    flags: IGNORECASE
```

Verify with `diag validate` or `GET /health` → `redaction_rules` count.

### `service_map.yaml`

**Purpose.** Declares services, dependency edges, and optional log routing.
This is the blast-radius graph.

**How it is used.**

- Expands “what else might be broken?” from the alerting service.
- Selects dependency PromQL probes via each node’s `kind`.
- `log_services` / `log_selector` redirect Loki queries when logs are not under
  the alert’s `service=` label.

Without a useful map the agent runs with an **empty** dependency map (no blast
radius). Install only seeds a starter topology — **edit it to match production
names**.

**How to configure.**

| Field | Meaning |
|---|---|
| `services.<name>` | Must match alert `service=` / Loki `service=` labels where possible |
| `kind` | Probe family: `http`, `gateway`, `monolith`, `database`, `redis`, … |
| `upstream` / `downstream` | Edges for blast radius |
| `description` | Optional human/LLM context |
| `log_services` | Extra Loki `service=` values to query |
| `log_selector` | Full stream selector override when needed |
| `module_dependencies` | Optional in-process module graph for monoliths |

See `examples/spring-modular-monolith/service_map.yaml` for a modular-monolith
shape.

### `execution_profile.yaml` (optional)

**Purpose.** The allowlist of actions the agent may run in its sandbox. This is
the only place an action can come from — presets ship **zero** actions, and
without this file the agent can never execute anything.

**How it is used.** When a runbook's
[`runbook-actions` block](../runbooks/README.md#executable-steps-runbook-actions)
names an `action_id`, the agent looks it up here, classifies it, and runs its
`argv` in a disposable container. Unknown ids, params outside their enum, and
services outside `scope.services` are denied before a container starts. The file
is read from the profile directory; `AGENT_EXEC_PROFILE_PATH` reports the
resolved path but does not relocate the lookup.

**How to configure.**

| Field | Meaning |
|---|---|
| `image` | Container image the actions run in — pin by digest in production |
| `actions[].id` | Name referenced by `runbook-actions` steps |
| `actions[].description` | Human context; also scanned by the destructive classifier |
| `actions[].argv` | Argv array, never a shell string. `{name}` tokens are substituted from `params` |
| `actions[].params.<name>.type` | `string` or `enum` |
| `actions[].params.<name>.values` | Allowed values for an `enum` param |
| `actions[].params.<name>.from` | Bind from incident state, e.g. `incident.service` |
| `actions[].scope.services` | Action may only target these services |
| `actions[].destructive` | `true` forces a classifier hold → escalate, never runs |
| `actions[].timeout_s` | Per-action kill deadline (default `60`) |

```yaml
version: 1
image: "ghcr.io/mskrado/diagnostic-agent-sandbox:1"
actions:
  - id: clear-cdn-cache
    description: "Purge the CDN edge cache for the affected service"
    argv: ["cache-purge", "--service", "{service}", "--scope", "edge"]
    params:
      service:
        type: enum
        from: "incident.service"
        values: ["web-gateway", "media-service"]
    scope:
      services: ["web-gateway", "media-service"]
    timeout_s: 60
```

Nothing here runs until `AGENT_EXEC_ENABLED=true` **and**
`AGENT_ROUTING_ENABLED=true`. See
[docs/design/sandboxed-execution.md](design/sandboxed-execution.md) for the
threat model and the container lockdown flags, and
`examples/spring-modular-monolith/execution_profile.yaml` for a complete file.

### `scenarios.yaml`

**Purpose.** Declares alert label sets paired with runbooks for
`diag lint` / `diag e2e` / `diag replay` (and related corpus checks).

**How it is used.** Offline lint checks coverage; e2e posts each scenario’s
labels to a running agent and scores the path; replay runs the labels through
the routing logic with no LLM or stack. It does **not** replace Prometheus rule
files (those live under install’s `observability/`).

**How to configure.** Keep `labels.alertname` / `service` aligned with both your
Alertmanager rules and runbook filenames. Install seeds scenarios from the
shipped alert catalog — trim or extend for your host.

A scenario may add an optional `replay:` block pinning the route decision it
should produce. Without it, replay expects `escalate` for `severity: critical`
and `report` otherwise:

```yaml
scenarios:
  - id: redis-connection-errors
    runbook: runbook-redis-connection-errors.md
    labels:
      alertname: RedisConnectionErrors
      service: platform-service
      severity: warning
    replay:
      expected_route: execute        # report | escalate | execute
      expected_runbook: runbook-redis-connection-errors.md
      confidence_note: high          # simulated LLM confidence for this case
```

See [eval/README.md](../eval/README.md#routing-replay-eval-diag-replay).

### `blind_eval.yaml` (optional)

**Purpose.** Synthetic cases (injected logs + ground truth) for
`diag eval blind`. Independent of runbooks so answers do not leak via RAG.

**How it is used.** Offline: logs go straight into the prompt. Live
(`--live-url` / `--loki-url`): logs are pushed to Loki, then `/alert` is fired.

Install workspaces often omit this file. Either add one, copy from
`eval/blind_eval_dataset.yaml`, or pass `--dataset` explicitly. See
[eval/README.md](../eval/README.md).

### `runbooks/`

**Purpose.** Markdown playbooks indexed for RAG when retrieval is enabled.

**How it is used.** Similarity search pulls excerpts into the LLM context for
the firing alert. Filenames are conventionally `runbook-<topic>.md`; scenarios
and alert annotations often reference them.

**How to configure.**

1. Keep install-seeded runbooks that match alerts you actually fire.
2. Add host-specific playbooks with clear titles and actionable steps.
3. Re-create / restart the agent (or rebuild the Chroma volume) after large
   corpus changes so the index refreshes.
4. `diag lint` checks corpus consistency without an LLM.

---

## Precedence

**Environment variables > manifest > profile files > built-in presets.**

Environment wins so a container can retarget a setting without editing the host
repository. `AGENT_PROFILE_DIR` and `AGENT_RUNBOOKS_PATH` are derived from the
workspace only when they are not already set. Runtime URLs and LLM settings
normally live in `agent/.env` from install — see [INSTALL.md](INSTALL.md).

## Redaction is fail-closed

See [`redaction.yaml`](#redactionyaml) above. Docker turns a missing mount
source into an empty directory, which would otherwise shadow your profile and
silently disable redaction — the fail-closed guard exists for that case.
`diag validate` and `GET /health` both report the rule count.

## Validating in CI

```bash
docker run --rm -v "$PWD/infrastructure/diagnostic-agent:/workspace:ro" \
  ghcr.io/mskrado/diagnostic-agent:<tag> sh -c "diag validate && diag lint"
```

`validate` covers configuration; `lint` covers content. Neither needs LLM
credentials or a running stack. Add `diag e2e --url` once an agent is deployed.
