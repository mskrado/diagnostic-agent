# hello-world workspace

The smallest complete workspace: a plain three-tier web app, six short files, no
code. Point the agent at this directory and it can diagnose an alert.

Use it to see what a workspace *is* before writing your own. Every file is a few
lines long and heavily commented. The full reference is
[docs/WORKSPACE.md](../../docs/WORKSPACE.md).

## The stack it describes

```
api  ──▶  app  ──▶  postgres
                └─▶  redis
```

- `api` — edge / reverse proxy
- `app` — the application itself
- `postgres`, `redis` — the database and cache it depends on

Metrics come from Prometheus using community naming (`http_requests_total`), and
logs come from Loki under a `service` label.

## What each file does

| File | What it tells the agent |
|---|---|
| `agent.yaml` | "This workspace is schema 1, and our metric names follow the `generic-prometheus` convention." Two lines, because everything else sits in this directory under its conventional name |
| `service_map.yaml` | The topology above: who calls whom, and what `kind` each service is. This is what lets the agent answer "what else might be broken?" |
| `metrics_profile.yaml` | Nothing but `extends: generic-prometheus` — the preset's PromQL for error rate, request rate, latency, and `up` already matches this stack |
| `logs_profile.yaml` | Logs live under the `service` label, are JSON, and `ERROR\|WARN` is the default gate. Two `alert_line_filters` entries mirror log-based alerts so the agent reads the same lines the ruler fired on |
| `prompt_profile.yaml` | A one-sentence description of the platform, plus copy-pasteable `curl` examples against Prometheus (`:9090`) and Loki (`:3100`) |
| `redaction.yaml` | Only `extends: generic-prometheus`, so outbound text keeps the preset's bearer-token and AWS-key scrubbing. Add tenant or PII rules here |
| `runbooks/runbook-high-error-rate.md` | What the team knows about 5xx spikes on `app`: first checks, common causes, blast radius — hypotheses only |

Not included, because they are optional: `scenarios.yaml`, `blind_eval.yaml`,
and `execution_profile.yaml`. See
[`../spring-modular-monolith/`](../spring-modular-monolith/) for a workspace with
an execution allowlist and a much richer topology.

## Run it

```bash
export AGENT_PROMETHEUS_URL=http://localhost:9090
export AGENT_LOKI_URL=http://localhost:3100

diag validate -w examples/hello-world
diag serve -w examples/hello-world --port 8000
```

Or with Docker — the image reads whatever you mount at `/workspace`:

```bash
docker run --rm -p 8001:8000 \
  -e AGENT_PROMETHEUS_URL=http://host.docker.internal:9090 \
  -e AGENT_LOKI_URL=http://host.docker.internal:3100 \
  -e AGENT_RAG_ENABLED=true \
  -v "$PWD/examples/hello-world:/workspace:ro" \
  ghcr.io/mskrado/diagnostic-agent:latest
```

Then fire a synthetic alert at it:

```bash
curl -X POST http://localhost:8000/alert -H 'Content-Type: application/json' -d '{
  "alerts": [{
    "status": "firing",
    "labels": {"alertname": "HighErrorRate", "service": "app", "severity": "warning"}
  }]
}'
```

`diag validate` works with no Prometheus, Loki, or LLM at all — it only checks
that these files resolve. A full diagnosis needs the data sources and a
configured model provider.

## Adapt it

Copy this directory into your own repository and change it in this order:

1. **`service_map.yaml`** — real service names, matching your alert `service=`
   labels exactly. This buys the most accuracy per minute spent.
2. **`agent.yaml`** — switch `extends:` to `spring-micrometer` if your apps
   expose Micrometer metrics. Unsure? Query Prometheus for
   `http_server_requests_seconds_count` and see whether it returns series.
3. **`runbooks/`** — replace the example with playbooks for failures you have
   actually had.
4. **`redaction.yaml`** — add rules for anything tenant-specific before you wire
   up Slack or email.
5. **`prompt_profile.yaml`** — your hostnames and ports, so suggested commands
   work when pasted.

[docs/WORKSPACE.md](../../docs/WORKSPACE.md) walks a single alert through every
one of these files if you want to see how they combine.
