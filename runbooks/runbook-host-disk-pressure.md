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

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
