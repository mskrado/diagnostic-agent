# `diag draft` — write the workspace the evidence supports

[`diag scan`](SCAN.md) tells you what your stack exposes. `diag draft` turns
that into the workspace files it implies: which preset to extend, which services
exist and what depends on what, how to find the logs, which alerts pair with
which runbook, and which redaction rules your logs actually need.

The rule it follows is simple, and it is the whole reason to trust the output:

> **Every value is checked against the live stack before it is written.**
> Anything that fails the check is still written — commented out, with the
> reason it failed.

So a drafted file is never a guess presented as configuration. It is a set of
confirmed values, plus a visible list of things your stack would not confirm.

```bash
# Stage a draft next to the workspace, then read the diff
diag draft -w infrastructure/diagnostic-agent --out ./diag-draft
diag validate -w ./diag-draft && diag lint -w ./diag-draft
```

Nothing is written into your workspace unless you pass `--in-place`, and even
then existing files are left alone unless you add `--force`.

## Why verification changes the problem

Almost every line in a workspace is a testable claim, and the stack itself is a
cheap oracle:

| Claim | How it is tested |
|---|---|
| This metrics template is right | Render it for a real service; it must return a vector |
| `service_label` is right | Query the label; it must return log lines |
| This service map node is real | It must have metrics or logs behind it |
| This dependency edge is real | The caller must expose client metrics for that dependency |
| This `alert_line_filters` regex is right | It must match lines in the recent window |
| `module_regex` is right | It must capture a group on most sampled lines |
| This redaction rule is worth adding | It must match real sampled lines |

That turns "write correct configuration for a stack I have never seen" from an
authoring problem into a search with an answer key.

## What it drafts

### Preset choice, measured

Instead of guessing a preset from a container name, `diag draft` renders each
built-in preset's metric suite against a real service and counts what comes
back:

```
preset scoring
  spring-micrometer        4/5 templates returned data (probe: platform-service) <- chosen
  generic-prometheus       1/4 templates returned data (probe: platform-service)
```

The probe service is deliberately an ordinary application rather than a gateway
or a datastore, so a template that fails really is missing rather than aimed at
the wrong kind of thing.

### `metrics_profile.yaml`

`extends:` the preset that scored highest, plus the overrides that verified.

The override that earns its keep: if your stack labels metrics by `job` (or
`app`, or `container`) rather than `service`, every preset template misses.
`diag draft` retargets each template at the label your stack actually uses and
verifies it, so a naming mismatch that would otherwise produce an agent with no
metrics is fixed automatically — and reported.

It also proposes `dependency_probes` per dependency kind, keeping the first
probe that returns data.

### `logs_profile.yaml`

Measured, not assumed: the `service_label` that overlaps your Prometheus service
names, `use_json_parser` from the proportion of lines that actually parse as
JSON, the real level field and the levels your logs really use, and a
`module_regex` derived from the longest common logger prefix.

`alert_line_filters` are not invented. A log-based alert's LogQL already
contains the regex that decides which lines matter, so the filter is extracted
from the ruler rule and then re-checked against the window.

### `service_map.yaml`

Nodes exist only when metrics or logs stand behind them. Edges come from the
best signal available:

1. **Tempo's service graph** (`traces_service_graph_request_total`) when the
   metrics generator is enabled. This is the only signal that *states* the call
   graph instead of implying it, so it wins outright.
2. **Client-side metric families** otherwise: a service exposing `hikaricp_*` is
   talking to a database, `lettuce_*` to Redis. Each inference is confirmed with
   a query for that service specifically.

`log_services` — the "Postgres does not ship logs to Loki, its errors land in
the application's stream" redirect — is filled in from what the scan
discovered by searching every stream for the dependency's name. That field is
the one operators are least likely to get right by hand, and it turns out to be
mechanically discoverable.

Edges that nothing proves, such as gateway-to-application routing, appear as a
commented block rather than a fact.

### `scenarios.yaml` and the runbooks

Alerts come from both rulers, with their real names and severities. Each is
paired to a runbook by the rule's `runbook` annotation first, then by matching
the alert name against the reference corpus, then by name overlap.

`diag lint` requires a runbook for every scenario and a scenario for every
runbook, so the pair is generated as a unit: matched runbooks are copied into
the draft, and an alert with no runbook is **reported rather than written**.

```
alerts with no runbook
  KafkaConsumerLag
  RedisEvictions

Write a runbook for these, then add the scenario. This is the corpus backlog,
measured.
```

Reference runbooks about services you do not run are reported as unused and not
copied, so you stop carrying runbooks for someone else's stack. Nothing is
deleted; the draft simply does not include them.

### `redaction.yaml`

Additive only. Each candidate rule comes with the number of times it matched
your sampled lines, so you are accepting evidence rather than a suggestion.
Patterns with a real false-positive cost — UUIDs, which are also trace and
request ids, and long digit runs — are proposed commented out.

## Reviewing a draft

The report ends with everything that was withheld:

```
withheld (written commented out)
  services.api-gateway.downstream: rejected: query returned no data
  latency_p99: rejected: query returned no data
  uuid: unverified: matched 20 time(s) on 9 sampled line(s); high false-positive risk
```

Each of those appears in the relevant file, commented, next to its reason. A
withheld value is not necessarily wrong — `latency_p99` failing usually means
histogram buckets are not being scraped — but it is not something the stack
would confirm, so it does not get to be live configuration.

Two invariants make the commenting safe rather than merely tidy:

- A section whose entries were **all** withheld is emitted entirely inside
  comments, key included. `templates:` with only commented children would parse
  as `templates: null` and silently replace the preset's whole metric suite with
  nothing.
- The same applies to `rules:` in `redaction.yaml`, where nulling the preset's
  baseline would leave zero redaction rules and stop the agent from starting.

## Options

| Flag | Default | Purpose |
|---|---|---|
| `-w, --workspace PATH` | discovered | Workspace to read URLs from (and write to with `--in-place`) |
| `--out DIR` | `./diag-draft` | Staging directory for the draft |
| `--in-place` | off | Write into the resolved workspace instead |
| `--force` | off | Allow overwriting files that already exist |
| `--bundle PATH` | none | Reuse a `diag scan --out` bundle instead of scanning again |
| `--prometheus-url URL` | from settings | Override the Prometheus base URL |
| `--loki-url URL` | from settings | Override the Loki base URL |
| `--alertmanager-url URL` | unset | Alertmanager base URL |
| `--window` | `5m` | Metrics window used when testing templates |
| `--lookback-minutes N` | 60 | Log window for sampling and for verifying selectors |
| `--dry-run` | off | Report what would be written without writing it |
| `--json` | off | Print the decision record as JSON |

A `--bundle` still needs a reachable Prometheus: the bundle supplies the
proposals, the live stack supplies the verdicts. Without an oracle, drafting
would be guessing, so `diag draft` exits non-zero rather than write unverified
files.

## What it does not do

- **No LLM.** Everything here follows from evidence, which keeps it usable on
  air-gapped installs.
- **No `execution_profile.yaml`.** A plausible-looking guess at an executable
  action is actively dangerous, and the sandbox design assumes a human chose
  those actions.
- **No `prompt_profile.yaml`, no runbook skeletons.** Those need a model and are
  tracked as later phases of
  [#119](https://github.com/mskrado/diagnostic-agent/issues/119).
- **No merge.** A draft is a set of files, not a patch. On a workspace a human
  has already edited, stage the draft and merge what you want.

## Related

- [SCAN.md](SCAN.md) — the evidence `diag draft` consumes
- [WORKSPACE.md](WORKSPACE.md) — what each workspace file is for
- [INSTALL.md](INSTALL.md) — `diag install` / `diag init` scaffolding
