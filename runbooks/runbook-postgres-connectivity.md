# Runbook: PostgresConnectivity (database unreachable)

**Alert:** `PostgresErrorsInLogs` — Loki rate of `{service="platform-service"} |~ "(?i)(postgres|jdbc|hikari|connection).*(refused|timeout|exhaust)"` > 0.1 for 5m.

## Meaning
`platform-service` cannot open new connections to PostgreSQL. This is distinct
from HikariCP pool *saturation* (see `runbook-db-pool-exhaustion.md`): here the
database itself is down, unreachable, rejecting auth, or timing out at the TCP
layer. Every module that touches the DB fails; 5xx rate climbs and readiness
probes flip. Correlate with `runbook-db-pool-exhaustion.md` and
`runbook-container-restart-loop.md` (postgres container).

## First checks
1. Logs: `{service="platform-service"} | json | level="ERROR" |~ "(?i)postgres|PSQLException|Connection refused"`.
2. Postgres container health: cAdvisor `container_last_seen{name=~".*postgres.*"}` and `container_start_time_seconds` (restart = crash/OOM).
3. Pool init failures at startup vs steady-state drops (`HikariPool - Exception during pool initialization`).
4. Confirm it is connectivity, not saturation: `hikaricp_connections_pending` near 0 but connections *failing* points to the DB, not the pool.

## Common causes
- **Postgres down / restarting** — OOM, disk full on the data volume, or crash loop.
- **Network partition** on the compose/overlay network (wrong host, DNS, firewall).
- **Auth failure after deploy** — rotated `POSTGRES_PASSWORD` / `SPRING_DATASOURCE_PASSWORD` mismatch (`FATAL: password authentication failed`).
- **max_connections reached on the server** (`FATAL: sorry, too many clients already`) — server-side limit, not the client pool.
- **TLS/sslmode mismatch** after config change.

## Blast radius
All DB-backed modules (auth, content, media, search metadata, analytics); the
gateway propagates 5xx to every tenant-facing route. Read replicas unaffected if
reads are routed separately.

## Example log lines (synthetic)
```json
{"@timestamp":"2026-07-20T20:02:05.112Z","level":"ERROR","logger_name":"org.postgresql.core.v3.ConnectionFactoryImpl","service":"platform-service","trace_id":"b2c3d4e5f6789012345678abcdef0123","message":"Connection to postgres:5432 refused. Check that the hostname and port are correct and that the postmaster is accepting TCP/IP connections."}
{"@timestamp":"2026-07-20T20:02:07.220Z","level":"ERROR","logger_name":"com.zaxxer.hikari.pool.HikariPool","service":"platform-service","trace_id":"c3d4e5f6789012345678abcdef012345","message":"HikariPool-1 - Exception during pool initialization. org.postgresql.util.PSQLException: FATAL: password authentication failed for user \"appuser\""}
{"@timestamp":"2026-07-20T20:02:31.044Z","level":"ERROR","logger_name":"org.springframework.jdbc.support.SQLErrorCodeSQLExceptionTranslator","service":"platform-service","trace_id":"d4e5f6789012345678abcdef01234567","message":"Could not open JDBC Connection for transaction; nested exception is org.postgresql.util.PSQLException: FATAL: sorry, too many clients already"}
{"@timestamp":"2026-07-20T20:03:01.900Z","level":"ERROR","logger_name":"org.hibernate.engine.jdbc.spi.SqlExceptionHelper","service":"platform-service","trace_id":"e5f6789012345678abcdef0123456789","message":"The connection attempt failed; nested exception is java.net.SocketTimeoutException: connect timed out to postgres/172.18.0.4:5432"}
```

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
