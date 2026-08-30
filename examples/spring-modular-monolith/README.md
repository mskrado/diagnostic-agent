# spring-modular-monolith workspace

A realistic workspace: an API gateway in front of a Spring Boot modular
monolith, with the backing stores and third-party APIs a real system accumulates.
Copy this one when your apps expose Micrometer metrics.

If you have not read a workspace before, start with
[`../hello-world/`](../hello-world/) — it is the same idea in six short files.
The full reference is [docs/WORKSPACE.md](../../docs/WORKSPACE.md).

## The stack it describes

```
frontend ──▶ api-gateway ──▶ platform-service ──▶ postgres, redis, elasticsearch,
   (Faro)      (:8000)          (:8080)              s3, openai, smtp, twilio
```

`platform-service` is one process containing logical modules — `auth`, `content`,
`media`, `search`, `ai`, `notification`, `analytics` — not a microservice mesh.
That distinction is why this example exists: it shows how to describe a monolith
so the agent can still reason about which part of it is failing.

## What makes it more interesting than hello-world

| File | What it demonstrates |
|---|---|
| `agent.yaml` | `extends: spring-micrometer`, the preset for Actuator / Micrometer naming (`http_server_requests_seconds_*`, HikariCP, JVM) |
| `metrics_profile.yaml` | One line. The preset already knows Spring's metric names, so there is nothing to override — including HikariCP pending connections as a `database` dependency probe |
| `service_map.yaml` | The real value of this example. See the four patterns below |
| `logs_profile.yaml` | `module_regex: 'c\.p\.([a-z]+)'` extracts the failing module from Spring `logger_name` fields, plus six `alert_line_filters` that mirror log-based alert rules |
| `redaction.yaml` | Tenant and PII rules (`tenant_id` values, `tenant-*` tokens, UUIDs) appended on top of the preset's secret scrubbing |
| `prompt_profile.yaml` | Platform description and tool-run hints written in this stack's vocabulary |
| `execution_profile.yaml` | An allowlist of two sandboxed actions — one ordinary, one marked `destructive: true` so it always escalates instead of running |

### Four topology patterns worth copying

`service_map.yaml` is where most of the learning is:

1. **A dependency that does not log under its own name.** `postgres`, `redis`,
   `elasticsearch`, `s3`, `openai`, `smtp`, and `twilio` all carry
   `log_services: [platform-service]`, because their errors surface in the
   application's log stream — there is no `postgres` process shipping to Loki.
2. **A logical alert target.** `security` is not a service at all; it is the
   `service=` label a Loki ruler attaches to JWT / CSRF / tenant-isolation
   anomalies. Its `log_services` points at the two processes that actually emit
   those lines.
3. **A different label entirely.** `frontend` uses
   `log_selector: '{app="frontend"}'` because browser (Faro) telemetry is not
   under `service=`.
4. **Modules inside one process.** `module_dependencies` maps each module to its
   backing stores, so when a log line identifies `c.p.media` as the failing
   module, the blast radius expands to `postgres` and `s3` specifically rather
   than to everything the monolith touches.

## Run it

```bash
diag validate -w examples/spring-modular-monolith
diag serve -w examples/spring-modular-monolith --port 8000
```

`diag validate` needs no Prometheus, Loki, or LLM — it only checks that these
files resolve, and reports the number of redaction rules in effect.

## What a real host would add

This example is profile-only, so it deliberately omits:

- `runbooks/` — your playbooks, indexed for retrieval
- `scenarios.yaml` — alert label sets paired with runbooks, for
  `diag lint` / `diag e2e` / `diag replay`
- `blind_eval.yaml` — synthetic cases with known root causes, for
  `diag eval blind`

Also note that `execution_profile.yaml` here is inert until a host sets both
`AGENT_ROUTING_ENABLED=true` and `AGENT_EXEC_ENABLED=true`, *and* a runbook
carries a matching
[`runbook-actions` block](../../runbooks/README.md#executable-steps-runbook-actions).
Shipped as-is, the agent can only report and escalate.
