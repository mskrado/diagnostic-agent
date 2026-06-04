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

## Past incident reference
See `incident-2025-03-11-pool-exhaustion.md`.
