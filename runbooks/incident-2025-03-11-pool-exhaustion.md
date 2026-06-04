# Post-mortem: 2025-03-11 — Pricing/content 5xx from pool exhaustion

**Impact:** ~15 min of elevated 5xx on `platform-service` (content module), p99
latency spike. Tenant content listing degraded.

**Root cause:** A schema migration held row locks on a hot table, causing
in-flight requests to wait on DB connections. The HikariCP pool saturated
(`hikaricp_connections_pending` rose from 0 to ~40), so new requests timed out
and returned 5xx.

**Detection:** `HighErrorRate` fired on `platform-service`. Loki showed
`c.p.content` "Connection is not available, request timed out" from 14:32.
`hikaricp_connections_pending` confirmed saturation; `pg_stat_activity` showed
the migration session holding locks.

**Resolution:** Migration completed / was rolled back, locks released, pool
recovered within 2 minutes.

**Lessons / prevention:**
- Run schema migrations in low-traffic windows; use `lock_timeout`.
- Add a dashboard panel + alert on `hikaricp_connections_pending > 0` for 2m.
- Prefer online/concurrent index builds.

**Tags:** database, hikaricp, migration, pool-exhaustion, content-module
