# Runbook: HostDiskPressure (node filesystem low)

**Alert:** `HostDiskSpaceLow` — `node_filesystem_avail_bytes / node_filesystem_size_bytes < 0.10` for 10m.

## Meaning
The EC2/DEV host is running out of disk. Docker layers, Loki/ES/Prometheus TSDB,
or audit logs may fill the volume and cause cascading failures.

## First checks
1. Prometheus: `node_filesystem_avail_bytes` by mountpoint.
2. `docker system df` on the host (manual); cAdvisor container logs growth.
3. Loki/ES/Prometheus retention vs volume size.

## Common causes
- Unbounded container logs without rotation.
- Loki/Prometheus retention too long for disk size.
- Diagnostic agent audit JSONL growth (`/app/audit`).

## Blast radius
All containers on the host; write-heavy services fail first (ES, Loki, Postgres).

## Example log lines (synthetic)
```json
{"@timestamp":"2026-07-20T20:14:05.000Z","level":"ERROR","logger_name":"com.publishi.platform.health.DiskSpaceHealthIndicator","service":"platform-service","trace_id":"6789abcdef01234567890123456789012","message":"Free disk space below threshold: path=/var/lib/docker free=1.2GB total=40GB (3%); writes may fail"}
{"@timestamp":"2026-07-20T20:14:12.410Z","level":"ERROR","logger_name":"org.hibernate.engine.jdbc.spi.SqlExceptionHelper","service":"platform-service","trace_id":"789abcdef01234567890123456789013","message":"could not write to file: No space left on device"}
```

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
