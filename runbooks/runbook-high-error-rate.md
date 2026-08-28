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

## Example log lines (synthetic)
```json
{"@timestamp":"2026-07-20T20:00:03.101Z","level":"ERROR","logger_name":"org.springframework.web.servlet.mvc.method.annotation.ExceptionHandlerExceptionResolver","service":"platform-service","trace_id":"11112222333344445555666677778888","message":"Resolved [java.lang.NullPointerException] for HTTP POST /api/v1/content — returning 500"}
{"@timestamp":"2026-07-20T20:00:05.204Z","level":"ERROR","logger_name":"com.example.platform.content.ContentService","service":"platform-service","trace_id":"22223333444455556666777788889999","message":"Unhandled exception publishing content item; 5xx rate rising for module=content"}
{"@timestamp":"2026-07-20T20:00:07.880Z","level":"ERROR","logger_name":"org.apache.catalina.core.ContainerBase.[Tomcat]","service":"platform-service","trace_id":"3333444455556666777788889999aaaa","message":"Servlet.service() threw exception; root cause java.util.concurrent.TimeoutException: Idle timeout expired 30000/30000 ms"}
```

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
