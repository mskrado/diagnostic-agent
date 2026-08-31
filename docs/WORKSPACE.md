# Host workspace reference

The agent ships as a generic Docker image that knows **nothing** about your
systems: not your service names, not your metric names, not where your logs
live. A **workspace** is the one directory where you tell it those things. It is
the only thing you have to write, and it is plain YAML and markdown that lives
in your own git repository.

## Think of it as onboarding a new engineer

Imagine a capable engineer joining your team on Monday. Before they can take a
turn on-call, they need a few things from you — and the workspace is exactly
that handover packet, written down in files instead of explained over coffee:

| What a new engineer needs to be told | Where the agent reads it |
|---|---|
| "Here is how our services fit together, and what breaks when one of them does" | [`service_map.yaml`](#service_mapyaml) |
| "This is how you check whether a service is healthy" | [`metrics_profile.yaml`](#metrics_profileyaml) |
| "The logs are here, and these are the lines worth reading" | [`logs_profile.yaml`](#logs_profileyaml) |
| "Here's what we learned the last five times this broke" | [`runbooks/`](#runbooks) |
| "Never put customer data or tokens in a ticket" | [`redaction.yaml`](#redactionyaml) |
| "This is our platform, described in our own words" | [`prompt_profile.yaml`](#prompt_profileyaml) |
| "These are the few actions you're allowed to take yourself" | [`execution_profile.yaml`](#execution_profileyaml-optional) |

The remaining two files are for *testing* the agent rather than running it:
[`scenarios.yaml`](#scenariosyaml) lists example alerts to replay, and
[`blind_eval.yaml`](#blind_evalyaml-optional) holds synthetic incidents with
known answers so you can score the agent's diagnoses offline.

Nothing here is code, and nothing here is secret — credentials and URLs live in
the agent's `.env`, not in the workspace.

You do not have to write the first version by hand. Prefer the evidence loop
([`diag scan`](SCAN.md) → [`diag draft`](DRAFT.md) → review →
[`diag drift`](DRIFT.md)) — see [Authoring workflow](#authoring-workflow-scan--draft--drift).
Read this page to understand what you are reviewing and what each file is for.

## The shape of a workspace

One directory, anywhere in your repository:

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

You then hand that one directory to the agent, and every command finds
everything inside it — no path arguments:

```bash
docker run --rm -v "$PWD/infrastructure/diagnostic-agent:/workspace:ro" \
  ghcr.io/mskrado/diagnostic-agent:<tag> diag validate
```

`diag install` writes the same layout under `deploy/agent/workspace/` as editable
stubs. Full install-bundle files (`.env`, compose, observability snippets) are
documented in [INSTALL.md](INSTALL.md#what-gets-generated).

The fastest way to make this concrete is to read a real one:
[`examples/hello-world/`](../examples/hello-world/) is a complete workspace for a
plain three-tier app, and each file is only a few lines long.

---

## Topics

1. [Authoring workflow: scan → draft → drift](#authoring-workflow-scan--draft--drift)
2. [What each file is for](#what-each-file-is-for)
3. [The smallest workspace that works](#the-smallest-workspace-that-works)
4. [Locating the workspace](#locating-the-workspace)
5. [How the agent uses the workspace](#how-the-agent-uses-the-workspace)
6. [Manifest (`agent.yaml`)](#manifest-agentyaml)
7. [Flat layout](#flat-layout)
8. [File-by-file reference](#file-by-file-reference)
   - [`metrics_profile.yaml`](#metrics_profileyaml)
   - [`logs_profile.yaml`](#logs_profileyaml)
   - [`prompt_profile.yaml`](#prompt_profileyaml)
   - [`redaction.yaml`](#redactionyaml)
   - [`service_map.yaml`](#service_mapyaml)
   - [`execution_profile.yaml`](#execution_profileyaml-optional)
   - [`scenarios.yaml`](#scenariosyaml)
   - [`blind_eval.yaml`](#blind_evalyaml-optional)
   - [`runbooks/`](#runbooks)
9. [When the workspace is wrong](#when-the-workspace-is-wrong)
10. [Precedence](#precedence)
11. [Redaction is fail-closed](#redaction-is-fail-closed)
12. [Validating in CI](#validating-in-ci)

---

## Authoring workflow: scan → draft → drift

| Step | Command | Writes files? | When |
|---|---|---|---|
| **Scan** | [`diag scan`](SCAN.md) | Optional JSON bundle only | Before authoring, and whenever you want a fresh picture of the stack |
| **Draft** | [`diag draft`](DRAFT.md) | Staging dir by default (`./diag-draft`) | Once you have evidence; optional `--llm` for prompt/runbook drafts |
| **Review** | `diag validate` / `diag lint` + human diff | Only what you merge | Before committing workspace changes |
| **Drift** | [`diag drift`](DRIFT.md) | No (gate only) | CI and scheduled checks after the workspace is in production |

```bash
diag scan -w infrastructure/diagnostic-agent --out ./scan-evidence.json
diag draft -w infrastructure/diagnostic-agent \
  --bundle ./scan-evidence.json --out ./diag-draft
diag validate -w ./diag-draft && diag lint -w ./diag-draft
# merge ./diag-draft into the workspace, then keep it honest:
diag drift -w infrastructure/diagnostic-agent
```

Install path (client fork under `client/workspace/`):
[INSTALL.md §5](INSTALL.md#5-customize-your-workspace). Manual-only bootstrap:
[INTEGRATING.md](INTEGRATING.md).

## What each file is for

Skim this table to get oriented; the
[file-by-file reference](#file-by-file-reference) has the full field lists.

| File | The question it answers | Needed? | If you leave it out |
|---|---|---|---|
| `agent.yaml` | "Which metric naming does this stack use, and where is everything?" | Recommended | Conventional layout is auto-detected and the `generic-prometheus` preset is assumed |
| `service_map.yaml` | "What talks to what?" | **Strongly recommended** | The agent diagnoses the alerting service in isolation — no blast radius, no dependency metrics |
| `metrics_profile.yaml` | "What PromQL measures error rate, latency, saturation here?" | Usually one line | Preset queries are used, which is fine if your metric names match the preset |
| `logs_profile.yaml` | "Which Loki label is the service, and which lines matter for this alert?" | Usually short | Defaults to `service` label and `ERROR\|WARN` lines |
| `prompt_profile.yaml` | "What is this platform, in your words?" | Recommended | The model reasons without your vocabulary, hostnames, or ports |
| `redaction.yaml` | "What must never leave the box?" | **Effectively required** | Preset secret scrubbing still applies, but zero resolved rules means the agent refuses to start ([fail-closed](#redaction-is-fail-closed)) |
| `runbooks/` | "What have we learned about these failures?" | **Strongly recommended** | Diagnoses rest on metrics and logs alone, with no institutional knowledge |
| `execution_profile.yaml` | "Which actions may the agent ever run itself?" | Optional | The agent can never execute anything — it can only report and escalate |
| `scenarios.yaml` | "Which alerts should we test against?" | Optional | `diag lint`, `diag e2e`, and `diag replay` skip the checks that need it |
| `blind_eval.yaml` | "Can we score its diagnoses offline?" | Optional | `diag eval blind` needs an explicit `--dataset` instead |

Two useful rules of thumb:

- **A file you omit is simply absent**, and tools skip the checks that need it.
  A path you *declare* in `agent.yaml` must exist — that is an error, not a
  fallback. This is what makes incremental adoption safe.
- **A file you provide does not have to be complete.** Every profile file starts
  from a built-in preset via `extends:`, so you only write the parts where your
  stack differs.

## The smallest workspace that works

You do not have to write all ten files. A useful workspace can be three:

```
diagnostic-agent/
├── agent.yaml          # schema: 1  +  extends: generic-prometheus
├── service_map.yaml    # your services and who calls whom
└── runbooks/           # one markdown file per failure you have seen
```

That gets you real blast-radius reasoning and your own institutional knowledge,
while metrics, logs, redaction, and prompt framing fall back to the preset. Then
grow it as you hit the limits:

| You notice | Add |
|---|---|
| Queries return nothing — your metric names differ from the preset | `metrics_profile.yaml` overrides |
| The log sample is empty or full of noise | `logs_profile.yaml` (`service_label`, `alert_line_filters`) |
| Reports contain tenant ids, emails, or internal hostnames | `redaction.yaml` rules |
| Suggested checks name services or ports you do not have | `prompt_profile.yaml` |
| You want the checks to run in CI | `scenarios.yaml`, then `blind_eval.yaml` |

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

### Worked example: one alert, file by file

Take the bundled [`examples/hello-world/`](../examples/hello-world/) workspace —
`api` (edge proxy) → `app` (the application) → `postgres` and `redis` — and
suppose Alertmanager posts this:

```json
{"alerts": [{"status": "firing", "labels": {
  "alertname": "HighErrorRate", "service": "app", "severity": "warning"}}]}
```

Here is what each file contributes, in the order the agent touches them.

**1. `service_map.yaml` — who else is involved?** The alert names `app`, and the
map says `app` is a `monolith` whose upstream is `api` and whose downstream is
`postgres` and `redis`. So the agent now has three things: the alerting service,
its **neighbours** (`api`, `postgres`, `redis`) to gather evidence about, and its
**blast radius** (`postgres`, `redis` — what may also be degraded).

**2. `metrics_profile.yaml` — what do I measure, and how?** Each service in the
map has a `kind`, and the metrics profile says which kinds get the standard
service suite. `app` (`monolith`) and `api` (`http`) qualify, so the agent
renders each template with that service name and a `5m` window. The preset's
`error_rate` template becomes real PromQL:

```promql
sum(rate(http_requests_total{service="app",code=~"5.."}[5m]))
  / clamp_min(sum(rate(http_requests_total{service="app"}[5m])), 0.001)
```

…and the same for `request_rate`, `latency_p99`, and `service_up`. `postgres`
and `redis` are *not* in the preset's `service_kinds` and `generic-prometheus`
ships no `dependency_probes`, so they contribute no metrics here — which is
exactly the gap `dependency_probes` exists to fill (the `spring-micrometer`
preset, for instance, probes HikariCP pending connections for `kind: database`).

**3. `logs_profile.yaml` — which log lines?** `service_label: service` makes the
Loki stream selector `{service="app"}`. There is no `alert_line_filters` entry
for `HighErrorRate`, so the agent falls back to the level gate:

```logql
{service="app"} | json | level=~"ERROR|WARN"
```

Had the alert been `PostgresErrorsInLogs`, the file's filter for that alertname
would have been used instead, replacing the level gate with a targeted regex so
that INFO lines matching the same pattern the Loki ruler fired on are still
included:

```logql
{service="app"} |~ "(?i)(postgres|jdbc|connection).*(refused|timeout)"
```

Note the selector is still `{service="app"}`: a `PostgresErrorsInLogs` alert
carries `service=postgres`, but the map's `log_services: [app]` on the
`postgres` node redirects the query, because Postgres itself does not ship logs
to Loki — its errors surface in the application's log stream. That redirect is a
detail only you can know, and it is why `service_map.yaml` matters so much.

**4. `runbooks/` — what do we already know?** The alert name, service, and the
error families found in those log lines become retrieval queries, which pull the
most similar chunks out of the corpus — here, `runbook-high-error-rate.md`. Those
excerpts go into the prompt as evidence the model may cite.

**5. `prompt_profile.yaml` — whose stack is this?** The model is told, in your
words, that this is "a simple 3-tier web application (api → app →
postgres/redis) observed via Prometheus and Loki", plus your copy-pasteable
`curl` examples against Prometheus on `:9090` and Loki on `:3100`. This is what
keeps suggested next steps phrased in commands that actually work here.

**6. `redaction.yaml` — what must not leave?** The finished report goes out to
Slack, email, PagerDuty, a Grafana annotation, and the audit log. Every one of
those passes through the resolved rules first. hello-world adds none of its own,
so it inherits the preset's bearer-token and AWS-key scrubbing.

The result: a structured report naming the likely cause, the evidence behind it,
and `postgres`/`redis` as the blast radius — assembled entirely from six small
files you can read in a couple of minutes.

Wrong preset or an empty/stub `service_map.yaml` does not crash the agent, but
live scoring against a real stack (metrics names, topology, hints) will be weak.

### One workspace per running agent

Steps 1–5 happen **once per process** and are cached for its lifetime, so a
running instance is bound to exactly one workspace and therefore one stack. Only
the per-alert `service` label varies between requests — the profile, topology,
redaction rules, and RAG index do not.

This is the intended sidecar model: run one agent per observability stack, each
with its own workspace, `.env`, and Alertmanager webhook URL, which keeps
credentials and retrieved runbooks scoped to a single environment. Editing
workspace files therefore requires a **restart or container recreate** to take
effect. See [README → Deployment model](../README.md#deployment-model-one-agent-per-stack).

## Manifest (`agent.yaml`)

`agent.yaml` is the table of contents. It is **optional** — a directory
following the conventional layout resolves identically — and exists so you can
pin a version, choose a preset, and point at non-default paths.

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

A preset is a set of naming conventions shipped **inside the image**, under
`app/profile/presets/`. Picking the right one is the highest-leverage line in
the whole workspace: it decides whether the agent's queries return numbers or
nothing. Your files then only need to state where your stack differs.

| Preset | Use when apps expose |
|---|---|
| `generic-prometheus` | Community `http_requests_total` / classic exporter naming |
| `spring-micrometer` | Spring Boot Actuator / Micrometer (`http_server_requests_seconds_*`, HikariCP, JVM) |

Not sure which? Query your Prometheus for `http_requests_total` and
`http_server_requests_seconds_count` and see which one returns series.

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
[`examples/spring-modular-monolith/`](../examples/spring-modular-monolith/). The
`profile/` subdirectory is worth it only when the workspace also holds a lot of
non-profile material and you want the separation.

---

## File-by-file reference

### `metrics_profile.yaml`

**In one line.** The PromQL the agent runs to see how a service is behaving.

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

| Field | Meaning |
|---|---|
| `templates` | Named PromQL strings. `{service}` and `{window}` are substituted per alert |
| `service_kinds` | Which `kind` values from `service_map.yaml` receive the standard service suite |
| `service_metrics` | Which template names make up that suite (error rate, latency, …) |
| `always_collect` | Templates always run on the alerting service, e.g. `db_pool_pending` |
| `dependency_probes` | `kind` → template name or inline PromQL, for backing stores that get no service suite |

Install writes a one-line stub. Prefer copying
`examples/spring-modular-monolith/metrics_profile.yaml` for Spring hosts.

### `logs_profile.yaml`

**In one line.** How to turn an alert into a Loki query that returns the
interesting lines and not the whole stream.

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

The single highest-value entry here is `alert_line_filters`: mirror the regex
your Loki ruler already uses for a log-based alert, and the agent reads the same
lines that fired it instead of a generic error sample.

### `prompt_profile.yaml`

**In one line.** Your stack described in your own words, so the model speaks
your vocabulary and suggests commands that exist.

**Purpose.** Host-specific framing injected into the LLM prompt:
`platform_description` and `tool_run_hints`.

**How it is used.** The model sees your stack vocabulary and suggested
investigation commands. Core safety rules (hypotheses-only, evidence grounding,
JSON schema) stay in agent code and **cannot** be overridden here.

**How to configure.** Prefer the coding-agent playbook
[`PROMPT_PROFILE_AUTHORING.md`](PROMPT_PROFILE_AUTHORING.md) (inventory →
naming matrix → golden/forbidden commands). Short version:

1. Describe the real architecture in `platform_description` (gateway, monolith,
   backing stores).
2. Put copy-pasteable curl / docker / actuator examples in `tool_run_hints`
   using **your** hostnames and ports — and keep alert `service=` labels,
   compose service keys, and `container_name` values in separate allowlists.
3. Keep hints honest: suggested human steps, not claims the agent already ran
   them.

### `redaction.yaml`

**In one line.** The regexes that scrub anything that must never appear in a
Slack message, an email, or the audit log.

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

| Field | Meaning |
|---|---|
| `name` | Rule identifier. Reusing a preset rule's name replaces that rule |
| `pattern` | Regex to match. Capture groups are referenced from `replacement` |
| `replacement` | Substitution, e.g. `\1[REDACTED]\2` to keep the surrounding text |
| `flags` | `IGNORECASE`, or empty for none |

Verify with `diag validate` or `GET /health` → `redaction_rules` count.

### `service_map.yaml`

**In one line.** Your architecture diagram, in a form the agent can traverse to
ask "what else might be broken?"

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

```yaml
services:
  api:
    kind: http
    downstream: [app]
    description: "Edge / reverse proxy"
  app:
    kind: monolith
    upstream: [api]
    downstream: [postgres, redis]
  postgres:
    kind: database
    upstream: [app]
    # Postgres does not ship logs to Loki; its errors land in the app stream.
    log_services: [app]
```

Three things worth getting right, because they are the ones that quietly cost
you accuracy:

- **Names must match your alert labels.** A node the alert's `service=` value
  cannot resolve to means no topology at all for that alert.
- **`kind` is what selects queries**, so a mislabelled node simply gets no
  metrics.
- **`log_services` / `log_selector` handle the common case where a thing does
  not log under its own name** — managed databases, external APIs, browser
  telemetry under an `app=` label, or a logical alert target like `security`.

See `examples/spring-modular-monolith/service_map.yaml` for a modular-monolith
shape, including a `module_dependencies` graph that widens the blast radius when
a log line identifies which module inside the monolith is failing.

### `execution_profile.yaml` (optional)

**In one line.** The short, explicit list of commands the agent is ever allowed
to run — absent this file, it can only report.

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

**In one line.** Example alerts, each paired with the runbook it should reach,
so the offline tools have something to check.

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

**In one line.** Synthetic incidents with known answers, used to score whether
the agent actually finds the right root cause.

**Purpose.** Synthetic cases (injected logs + ground truth) for
`diag eval blind`. Independent of runbooks so answers do not leak via RAG.

**How it is used.** Offline: logs go straight into the prompt. Live
(`--live-url` / `--loki-url`): logs are pushed to Loki, then `/alert` is fired.

Install workspaces often omit this file. Either add one, copy from
`eval/blind_eval_dataset.yaml`, or pass `--dataset` explicitly. See
[eval/README.md](../eval/README.md).

### `runbooks/`

**In one line.** Your team's accumulated knowledge, in markdown, retrieved by
similarity when an alert looks like something you have seen before.

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

Authoring rules, chunking behaviour, and the `runbook-actions` block are in
[runbooks/README.md](../runbooks/README.md). A runbook is also the most valuable
thing you can contribute upstream — see
[CONTRIBUTING.md](../CONTRIBUTING.md#the-contribution-loop-preferred).

---

## When the workspace is wrong

Most workspace mistakes are quiet rather than fatal, which is why
`diag validate` exists. The common ones:

| Symptom | Likely cause | Fix |
|---|---|---|
| Agent refuses to start, complains about redaction | The profile resolved to zero rules — usually a mis-pointed or empty mount, so nothing was found | Check the mount path; see [fail-closed](#redaction-is-fail-closed) |
| Agent refuses to start, names a YAML file | A profile file exists but does not parse. This is deliberate: a broken overlay must not silently fall back to preset-only config | Fix the YAML; `diag validate` reports the same error |
| Report has no blast radius | No `service_map.yaml`, or the alert's `service=` value is not a node in it | Add the node, matching the alert label exactly |
| Metrics are all empty | Preset does not match your metric naming, or the node's `kind` is not in `service_kinds` | Check `extends:`; query Prometheus for the metric name the preset uses |
| A dependency contributes no metrics | Its `kind` has no `dependency_probes` entry | Add one, or copy the `spring-micrometer` preset's |
| Log sample is empty | Wrong `service_label`, or the thing does not log under its own name | Set `log_services` / `log_selector` on that node |
| Log sample is noise | No `alert_line_filters` entry for that alertname | Mirror your Loki ruler's regex |
| Edits appear to do nothing | The profile is cached for the process lifetime | Restart or recreate the container |
| `diag validate` warns about a version mismatch | `agent_version` predates the running image | Review the changelog, then bump the pin |

## Precedence

**Environment variables > manifest > profile files > built-in presets.**

Environment wins so a container can retarget a setting without editing the host
repository. `AGENT_PROFILE_DIR` and `AGENT_RUNBOOKS_PATH` are derived from the
workspace only when they are not already set. Runtime URLs and LLM settings
normally live in `agent/.env` from install — see [INSTALL.md](INSTALL.md).

One exception to the "later wins" rule: `redaction.yaml` **rules accumulate**
along the chain instead of replacing each other, so extending a preset can never
lose its secret scrubbing.

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

To fail when the workspace no longer matches Prometheus/Loki (new services,
dark map nodes, uncovered alerts, dead templates), add [`diag drift`](DRIFT.md):

```bash
diag drift -w infrastructure/diagnostic-agent
# or, with a committed/CI-produced scan bundle:
diag drift -w infrastructure/diagnostic-agent \
  --bundle ./scan-evidence.json --no-oracle
```
