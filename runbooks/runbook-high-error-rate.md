# Runbook: HighErrorRate (5xx spike)

**Alert:** `HighErrorRate` — `rate(http_server_requests_seconds_count{status=~"5.."}[5m]) / rate(http_server_requests_seconds_count[5m]) > 0.05` for 5m.

## Meaning
More than 5% of HTTP responses from a service (`api-gateway` or `platform-service`)
are 5xx. Because `platform-service` is a modular monolith, a single failing
module (auth, content, media, search, ai, notification, analytics) can drive the
whole service's error rate up.

## First checks
1. Identify the failing module from logs: `{service="platform-service"} | json | level="ERROR"`
   and look at `logger_name` (e.g. `c.p.content.*`).
2. Check backing dependencies for the implicated module:
   - DB: `hikaricp_connections_pending{service="platform-service"}` > 0 means pool saturation.
   - Redis: connection/timeout errors in logs.
   - Elasticsearch: cluster red/yellow (search module).
   - S3/OpenAI/SMTP/Twilio: external 4xx/5xx or timeouts in logs.
3. Confirm the gateway itself isn't the source (`service="api-gateway"`).

## Common causes
- **DB connection pool exhaustion** — long-running query/migration holding connections.
  Evidence: `hikaricp_connections_pending` rising, "pool exhausted"/"connection timeout" logs.
- **Downstream external API failure** — OpenAI/S3/SMTP/Twilio outage causing cascading 5xx.
- **Recent deploy** — error rate steps up right after a release.
- **Unhandled exception in a single endpoint** — concentrated on one `uri` label.

## Blast radius
If `platform-service` is degraded, `api-gateway` requests fronting it return 5xx.
Tenant-facing features for the affected module degrade. Check downstream stores.

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
