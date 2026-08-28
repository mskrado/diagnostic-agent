# Runbook: SecurityAuthAnomaly (authentication & tenant-isolation signals)

**Alert:** `SecurityAuthErrorsInLogs` — Loki rate of `{service=~"platform-service|api-gateway"} |~ "(?i)(jwt|csrf|access denied|authentication failed|cross-tenant|account locked)"` > 0.2 for 5m.

## Meaning
An elevated rate of security-relevant events in the `auth` module or gateway.
This is usually benign (users mistyping passwords, expired tokens) but a spike can
indicate credential stuffing, a broken auth deploy, misconfigured CORS/CSRF, or —
most importantly for a multi-tenant platform — **attempted cross-tenant access**.
The agent surfaces hypotheses only; it never blocks users or IPs.

## First checks
1. Logs: `{service="platform-service"} | json | logger_name=~".*security.*|.*auth.*" | level=~"WARN|ERROR"`.
2. Distinguish shapes:
   - `JWT expired` / `Invalid JWT` — token lifecycle or clock skew.
   - `Account locked` bursts from one `ip=` — brute force / credential stuffing.
   - `Denied cross-tenant access` — tenant isolation filter rejected a request (investigate, do not ignore).
   - `Invalid CSRF token` / CORS `Reject: Origin` — frontend/gateway misconfig after deploy.
3. Correlate with a recent deploy (`api-gateway` CORS/allowed-origins or JWT issuer change) and with `HighErrorRate` on 401/403.

## Common causes
- **Credential stuffing / brute force** — many `Account locked` / 401s from few IPs.
- **JWT issuer / key rotation** breaking token validation (`kid` mismatch, expired signing key).
- **Clock skew** between issuer and validator causing spurious `JWT expired`.
- **CORS/CSRF misconfiguration** after a frontend or gateway release.
- **Cross-tenant probing** — `TenantIsolationFilter` denials indicate a client (or bug) reaching another tenant's data.

## Blast radius
`auth` module and every authenticated route via the gateway. Tenant isolation
denials are contained (access is blocked) but signal a correctness/security issue
worth escalating. Note: `tenantId`, `tenant-*` tokens, and UUIDs are redacted from
audit/annotation output — the hypothesis is preserved without leaking tenant identity.

## Example log lines (synthetic)
```json
{"@timestamp":"2026-07-20T20:11:10.200Z","level":"WARN","logger_name":"org.springframework.security.oauth2.jwt.NimbusJwtDecoder","service":"platform-service","trace_id":"cdef0123456789abcdef0123456789012","message":"JWT expired at 2026-07-20T19:55:00Z; current time 2026-07-20T20:11:10Z; authentication failed"}
{"@timestamp":"2026-07-20T20:11:22.331Z","level":"WARN","logger_name":"com.example.platform.security.LoginAttemptService","service":"platform-service","trace_id":"def0123456789abcdef01234567890123","message":"Account locked after 5 failed login attempts for user=editor@example.com from ip=203.0.113.44"}
{"@timestamp":"2026-07-20T20:11:55.670Z","level":"ERROR","logger_name":"com.example.platform.security.TenantIsolationFilter","service":"platform-service","trace_id":"f0123456789abcdef0123456789012345","tenantId":"tenant-alpha","message":"Denied cross-tenant access: authenticated tenant attempted to read a resource owned by another tenant on path=/api/v1/content/99"}
{"@timestamp":"2026-07-20T20:12:08.901Z","level":"WARN","logger_name":"org.springframework.security.access.AccessDeniedException","service":"platform-service","trace_id":"0123456789abcdef01234567890123456","message":"Access Denied: user lacks required authority ROLE_ADMIN for POST /api/v1/tenants"}
{"@timestamp":"2026-07-20T20:12:20.444Z","level":"ERROR","logger_name":"org.springframework.web.cors.DefaultCorsProcessor","service":"api-gateway","trace_id":"123456789abcdef012345678901234567","message":"Reject: Origin 'https://evil.example' is not allowed by CORS configuration"}
```

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
