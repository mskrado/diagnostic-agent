# Integrating diagnostic-agent into your project

This guide shows how to wire the agent into an existing stack **without
modifying agent code**. You only supply an **integration profile** and
environment variables.

## 1. Choose a distribution

| Option | When to use |
|---|---|
| **Docker image** (`ghcr.io/mskrado/diagnostic-agent`) | Sidecar next to Prometheus / Loki / Alertmanager |
| **pip package** (`pip install diagnostic-agent`) | Embed in a Python host or run `diagnostic-agent serve` |

## 2. Create an integration profile

Copy `examples/hello-world/` and edit:

```text
my-profile/
  service_map.yaml       # your services + dependencies
  metrics_profile.yaml   # extends: generic-prometheus | spring-micrometer
  logs_profile.yaml      # Loki label + optional alert line filters
  redaction.yaml         # tenant / PII scrubbing
  prompt_profile.yaml    # how the LLM should describe your stack
  runbooks/              # optional markdown playbooks for RAG
```

### service_map.yaml

```yaml
services:
  api:
    kind: http
    upstream: []
    downstream: [app]
  app:
    kind: monolith
    upstream: [api]
    downstream: [postgres]
  postgres:
    kind: database
    upstream: [app]
    log_services: [app]   # logs emitted by app, not a postgres process
module_dependencies: {}
```

`kind` selects which PromQL probes run (from your metrics profile). Presets ship
naming conventions only, so topology always comes from your profile — without a
`service_map.yaml` the agent runs with an empty dependency map (no blast radius).

### metrics_profile.yaml

Prefer extending a built-in preset:

```yaml
extends: spring-micrometer   # or generic-prometheus
```

Or override individual templates (`{service}` / `{window}` placeholders):

```yaml
extends: generic-prometheus
templates:
  error_rate: >-
    sum(rate(http_requests_total{{service="{service}",code=~"5.."}}[{window}]))
    / clamp_min(sum(rate(http_requests_total{{service="{service}"}}[{window}])), 0.001)
```

### logs_profile.yaml

```yaml
extends: generic-prometheus
service_label: service
level_filter: "ERROR|WARN"
module_regex: null          # e.g. 'c\.p\.([a-z]+)' for Spring packages
alert_line_filters:
  PostgresErrorsInLogs: "(?i)(postgres|connection).*(refused|timeout)"
```

### redaction.yaml

Rules **accumulate** along the `extends:` chain, so your tenant/PII rules are
appended to the base preset's secret scrubbing rather than replacing it. Reuse a
parent rule's `name` to override it.

```yaml
extends: generic-prometheus   # keeps bearer_token + aws_access_key
rules:
  - name: tenant_kv
    pattern: '("?tenant[_-]?id"?\s*[:=]\s*")[^"]*(")'
    replacement: '\1[REDACTED]\2'
    flags: IGNORECASE
```

Redaction is **fail-closed**: the agent refuses to start when the resolved
profile has zero rules, so a mis-pointed `AGENT_PROFILE_DIR` cannot silently emit
raw data. Check the count with `diagnostic-agent health-check` or `GET /health`;
`AGENT_REQUIRE_REDACTION=false` opts out.

> Never bind-mount a profile directory over one that already exists inside the
> image. Docker creates an empty directory when the host path is missing, and the
> empty mount shadows the real profile.

### prompt_profile.yaml

Put a short description of *your* platform and copy-pasteable tool hints here.
Core safety rules (hypotheses-only, evidence grounding, JSON schema) stay in
agent code and cannot be overridden.

## 3. Wire Alertmanager

```yaml
receivers:
  - name: diagnostic-agent
    webhook_configs:
      - url: http://diagnostic-agent:8000/alert
        send_resolved: false
route:
  routes:
    - matchers: [severity =~ "warning|critical"]
      receiver: diagnostic-agent
```

## 4. Docker Compose snippet

```yaml
services:
  diagnostic-agent:
    image: ghcr.io/mskrado/diagnostic-agent:latest
    # For local iteration against this repo:
    # build: ./diagnostic-agent
    ports:
      - "8001:8000"
    environment:
      AGENT_PROFILE_DIR: /profile
      AGENT_DEFAULT_PRESET: generic-prometheus
      AGENT_PROMETHEUS_URL: http://prometheus:9090
      AGENT_LOKI_URL: http://loki:3100
      AGENT_CHAT_PROVIDER: ${AGENT_CHAT_PROVIDER:-ollama}
      AGENT_CHAT_MODEL: ${AGENT_CHAT_MODEL:-mistral:7b-instruct}
      AGENT_RAG_ENABLED: "true"
    volumes:
      - ./my-profile:/profile:ro
```

## 5. Verify

```bash
curl http://localhost:8001/health
# -> {"status":"ok","profile":"my-profile","preset":"generic-prometheus",
#     "redaction_rules":3,"service_map":true,"models":{...}}
# "status":"degraded" / "redaction_rules":0 means the profile was not found.

curl -X POST http://localhost:8001/alert -H 'Content-Type: application/json' \
  -d '{"alerts":[{"status":"firing","labels":{"alertname":"HighErrorRate","service":"app","severity":"warning"}}]}'
```

## Reference: Spring Boot modular monolith

[`examples/spring-modular-monolith/`](../examples/spring-modular-monolith/) is a
complete Spring Boot modular-monolith profile (Micrometer metrics, tenant
redaction, gateway + backing stores). Copy it and adapt the YAML for your host.
