# Runbook: Database Connection Pool Saturation

## Symptoms
- `hikaricp_connections_pending{service="platform-service"} > 0` (threads waiting).
- Logs contain "Connection is not available", "pool exhausted", or JDBC timeouts.
- Latency p95/p99 climb; 5xx rate rises (drives `HighErrorRate`).

## Likely causes
1. **Long-running query or migration** holding connections / row locks.
   Check: `SELECT * FROM pg_stat_activity WHERE state != 'idle' ORDER BY query_start;`
2. **Connection leak** — connections borrowed but never returned (missing close /
   unclosed transaction). HikariCP `hikaricp_connections_active` stays pinned high.
3. **Undersized pool** for current load — `hikaricp_connections_max` too low.
4. **Slow downstream** (e.g. PG under disk/CPU pressure) increasing hold time.

## Verification steps (read-only)
- Compare `hikaricp_connections_active` vs `hikaricp_connections_max`.
- Inspect the slowest queries in `pg_stat_activity`.
- Correlate the start time of pending connections with deploy/migration events.

## Example log lines (synthetic)
```json
{"@timestamp":"2026-07-20T20:01:12.341Z","level":"ERROR","logger_name":"com.zaxxer.hikari.pool.HikariPool","service":"platform-service","trace_id":"a1b2c3d4e5f6789012345678abcdef01","message":"HikariPool-1 - Connection is not available, request timed out after 30000ms (total=20, active=20, idle=0, waiting=47)"}
{"@timestamp":"2026-07-20T20:01:12.355Z","level":"ERROR","logger_name":"org.springframework.jdbc.support.SQLErrorCodeSQLExceptionTranslator","service":"platform-service","trace_id":"a1b2c3d4e5f6789012345678abcdef01","message":"Unable to acquire JDBC Connection; nested exception is java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available, request timed out after 30000ms"}
{"@timestamp":"2026-07-20T20:03:01.044Z","level":"ERROR","logger_name":"org.hibernate.engine.jdbc.spi.SqlExceptionHelper","service":"platform-service","trace_id":"d4e5f6789012345678abcdef01234567","message":"ERROR: deadlock detected; Process 1842 waits for ShareLock on transaction 99102 blocked by process 1839"}
```
Pool *saturation* (active=max, waiting>0) points here; connections *failing to
open* point to `runbook-postgres-connectivity.md` instead.

## Past incident reference
See `incident-2025-03-11-pool-exhaustion.md`.
