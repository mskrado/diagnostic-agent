# Runbook: GatewayUpstreamErrors (web server / reverse-proxy 5xx)

**Alert:** `ErrorLogSpike` on `service="api-gateway"` — `sum by (service) (rate({service="api-gateway"} | json | level="ERROR" [5m])) > 0.5` for 5m.

## Meaning
The Spring Cloud Gateway (reverse proxy fronting `platform-service`) is returning
5xx or failing to reach its upstream. Unlike `HighErrorRate` (which is upstream
application 5xx), this focuses on the **web-server/proxy layer**: read timeouts,
connection resets, 502/503, and rate-limit rejections at the edge. Users see
errors even when the upstream may be merely slow.

## First checks
1. Logs: `{service="api-gateway"} | json | level=~"ERROR|WARN"` and read `logger_name`
   (e.g. `NettyRoutingFilter`, `HttpClientConnect`).
2. Separate proxy failures from upstream 5xx:
   - `ReadTimeoutException` / `Idle timeout` → upstream `platform-service` slow (check JVM/DB).
   - `Connection prematurely closed` / `Connection reset` → upstream crashed mid-request (check container restarts).
   - `No healthy upstream` / 502/503 → discovery/health failing.
   - `429 Too Many Requests` → gateway rate limiter engaged (Redis-backed — check Redis too).
3. Correlate with `runbook-jvm-gc-pressure.md`, `runbook-db-pool-exhaustion.md`, and `runbook-container-restart-loop.md`.

## Common causes
- **Upstream latency** — platform-service slow (GC pause, DB pool, slow query) tripping gateway read timeout.
- **Upstream crash / restart** — connection reset by peer, premature close.
- **Rate limiter** rejecting bursts (429), possibly a Redis dependency issue.
- **Misrouted / missing route** after a deploy — 404/503 for a path.
- **TLS / header size / max in-flight** limits at the proxy.

## Blast radius
Every tenant-facing route flows through the gateway, so edge errors affect the
whole surface even if only one upstream module is unhealthy.

## Example log lines (synthetic)
```json
{"@timestamp":"2026-07-20T20:08:01.010Z","level":"ERROR","logger_name":"org.springframework.cloud.gateway.filter.NettyRoutingFilter","service":"api-gateway","trace_id":"3456789abcdef0123456789abcdef0123","message":"500 Server Error for HTTP GET \"/api/v1/content\"; nested exception is io.netty.handler.timeout.ReadTimeoutException: upstream platform-service:8080 read timed out"}
{"@timestamp":"2026-07-20T20:08:15.220Z","level":"ERROR","logger_name":"reactor.netty.http.client.HttpClientConnect","service":"api-gateway","trace_id":"456789abcdef0123456789abcdef01234","message":"Connection prematurely closed BEFORE response; Connection reset by peer to platform-service:8080"}
{"@timestamp":"2026-07-20T20:08:30.501Z","level":"WARN","logger_name":"org.springframework.cloud.gateway.filter.factory.RequestRateLimiterGatewayFilterFactory","service":"api-gateway","trace_id":"56789abcdef0123456789abcdef012345","message":"Request rate limited for path=/api/v1/auth/login; remaining=0; returning 429 Too Many Requests"}
{"@timestamp":"2026-07-20T20:08:44.880Z","level":"ERROR","logger_name":"org.springframework.cloud.gateway.handler.FilteringWebHandler","service":"api-gateway","trace_id":"6789abcdef0123456789abcdef0123456","message":"502 BAD_GATEWAY: No healthy upstream for route platform-service"}
```

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
