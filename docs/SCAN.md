# `diag scan` — see what the agent can see

Writing a workspace means answering questions about your stack: what are the
services, what are the metrics called, where do the logs live, which alerts
exist. `diag scan` answers those questions by asking your stack directly, and
prints what it found.

It is **read-only**. It writes no workspace files and changes nothing in
Prometheus, Loki, or Alertmanager. Run it before you write a workspace, to find
out what you are working with; run it afterwards to check the workspace still
matches reality.

```bash
# Against a stack on the Docker network the agent already joins
diag scan -w infrastructure/diagnostic-agent \
    --alertmanager-url http://alertmanager:9093
```

Prometheus and Loki URLs come from `AGENT_PROMETHEUS_URL` / `AGENT_LOKI_URL`, a
cwd `.env`, or package defaults (`http://prometheus:9090`,
`http://loki:3100`) — **not** from `client/agent/.env` and not from install
discovery. A workspace that already works with those env vars needs no URL
flags. Alertmanager has no default: the agent receives its webhook rather than
calling it, so pass `--alertmanager-url` when you want that section.

### One-shot Docker (no host `diag`)

Same pattern as [INSTALL Option B](INSTALL.md#option-b--one-shot-docker-no-usable-host-python-typical-amazon-linux-2).
With `--network host`, Compose DNS names do not resolve — pass published host
ports:

```bash
run_diag() {
  docker run --rm -v "$PWD:/work" -w /work --network host python:3.12-slim \
    bash -c "pip install -q -e . && $*"
}

run_diag "diag scan -w client/workspace --out ./scan-evidence.json \
  --prometheus-url http://127.0.0.1:9090 \
  --loki-url http://127.0.0.1:3100 \
  --alertmanager-url http://127.0.0.1:9093"
```

Or attach to the stack Compose network and keep `http://prometheus:9090` /
`http://loki:3100` / `http://alertmanager:9093`.

## What it reports

### Sources

Reachability, version, and volume per source. Each source degrades on its own —
an unreachable Loki costs you the log sections, not the scan. The exit code is
non-zero only when Prometheus is unreachable, since without metrics there is
nothing to correlate against.

### Service candidates

Every name that looks like a service, and whether it appears in metrics, in
logs, or both:

```
  api-gateway       metrics=yes  logs=yes  kind~gateway
  platform-service  metrics=yes  logs=yes
  postgres          metrics=yes  logs=no   kind~database  logs_under=platform-service
```

`logs_under` is the interesting one. A managed database, a third-party API, or
any dependency that does not ship logs under its own name still has failures —
they surface in the log stream of whatever talks to it. The scan finds that
stream by searching for the dependency's name across all streams and seeing
which one answers. That is the `log_services` redirect in `service_map.yaml`,
which is the field operators are least likely to guess correctly, discovered
rather than guessed.

`kind~` is a hint from the name, not a decision.

Names belonging to the observability stack itself (Prometheus, Loki, Grafana,
exporters, the agent) are filtered out: they are not what you want diagnosed.

### Metric naming markers

Whether metrics that identify a convention are present — for example
`http_server_requests_seconds_count` (Spring Boot / Micrometer) versus
`http_requests_total` (community naming). This tells you which preset your
`metrics_profile.yaml` should extend, and which dependency exporters are
already in place. The scan reports the evidence; choosing the preset is still
yours.

### Alerts

Every alerting rule from the Prometheus ruler *and* the Loki ruler, with
severity, the `runbook` annotation when there is one, and whether the alert is
firing right now. Then the number that matters:

```
workspace coverage: 14 alert(s) have a scenario, 9 do not
  no scenario: KafkaConsumerLag, RedisEvictions, ...
```

That is your corpus backlog, measured rather than estimated. An alert with no
scenario and no runbook is an alert the agent can only reason about generically.

For Loki rules the scan also extracts the line filters out of the LogQL
expression (`|~ "(?i)(postgres|jdbc).*(refused)"`). A log-based alert already
states the regex that decides which lines matter, so those go straight into
`logs_profile.yaml` — no guessing required.

### Log shape

Which labels exist and how many values each has, which label lines up with the
Prometheus service names (your `service_label`), what proportion of lines parse
as JSON (your `use_json_parser`), which field carries the level, and the logger
prefixes present. These are exactly the fields `logs_profile.yaml` asks for.

### Sensitive patterns

A census of what the sampled lines contain — emails, UUIDs, tokens, tenant
identifiers, credentials in URLs:

```
  email      412 line(s), 511 match(es)  email address
  uuid       301 line(s), 640 match(es)  UUID (often a tenant, user, or request id)
```

Every one of those is a candidate rule for `redaction.yaml`. Counts are taken
from the raw lines, *before* scrubbing, so the report tells you what is in your
logs; anything the scan holds on to has already been scrubbed.

## Safety

A scan reads production logs, so it treats them carefully:

- **Sampled lines are scrubbed twice.** First with your workspace's
  `redaction.yaml` rules when a workspace resolves, then with a fixed built-in
  pattern set. The built-in set does not depend on the workspace on purpose: you
  often run a scan precisely because you do not yet know what your redaction
  rules should be.
- **Lines are not kept unless you ask.** By default the bundle holds the derived
  facts and the census, not the prose. `--keep-lines` keeps up to ten scrubbed
  lines per stream so you can see what the agent sees.
- **`--no-samples` reads no log lines at all.** You still get labels, alerts,
  metrics, and topology.
- **The Alertmanager configuration is never captured.** `/api/v2/status` returns
  the full config, which routinely embeds Slack and PagerDuty URLs with tokens.
  Receiver names come from `/api/v2/receivers` instead.
- **Writing the bundle is opt-in.** Without `--out`, nothing touches disk. When
  you do write one, keep it out of version control until you have read it.

## Options

| Flag | Default | Purpose |
|---|---|---|
| `-w, --workspace PATH` | discovered | Workspace for URLs, redaction rules, and alert-coverage comparison |
| `--prometheus-url URL` | from settings | Override the Prometheus base URL |
| `--loki-url URL` | from settings | Override the Loki base URL |
| `--alertmanager-url URL` | unset (skipped) | Alertmanager base URL |
| `--out PATH` | none | Write the evidence bundle as JSON |
| `--json` | off | Print the bundle instead of the report |
| `--lookback-minutes N` | 60 | Log sample window |
| `--sample-lines N` | 300 | Total lines sampled, spread across streams |
| `--max-services N` | 12 | Cap on streams sampled and dependencies probed |
| `--no-samples` | off | Read no log lines |
| `--keep-lines` | off | Keep scrubbed sample lines in the bundle |
| `--verbose` | off | Show every alert and every naming marker |

## The evidence bundle

`--out` writes a schema-versioned JSON document with four sections —
`prometheus`, `loki`, `alertmanager`, and `findings` (the cross-referenced
conclusions the report renders). The shape is a deliberate contract:
[`diag draft`](DRAFT.md) consumes a bundle to draft workspace files, and it must
be able to read one produced by an older scan. A bundle whose schema is newer
than the agent understands is refused rather than half-read.

## What it does not do

It does not write or modify workspace files, does not call an LLM, and does not
decide anything on your behalf. Turning evidence into drafted
`service_map.yaml` / `logs_profile.yaml` / `metrics_profile.yaml` files is the
job of [`diag draft`](DRAFT.md).

## Related

- [DRAFT.md](DRAFT.md) — turning this evidence into workspace files
- [DRIFT.md](DRIFT.md) — gating a workspace against fresh evidence
- [WORKSPACE.md](WORKSPACE.md) — what each workspace file is for
- [INSTALL.md](INSTALL.md) — `diag install` / `diag init` scaffolding
- [PROMPT_PROFILE_AUTHORING.md](PROMPT_PROFILE_AUTHORING.md) — writing the prompt profile
