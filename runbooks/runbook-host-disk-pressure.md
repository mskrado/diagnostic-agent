# Runbook: HostDiskPressure (node filesystem low)

**Alerts:**
- `HostDiskSpaceLow` — `node_filesystem_avail_bytes / size < 0.10` for 10m
- `HostDiskSpaceCritical` — same ratio `< 0.05` for 5m (severity critical)
- `HostDiskFillPredicted` — `predict_linear(...[6h], 24h) < 0` for 1h

## Meaning
The EC2/DEV host volume is exhausting space and/or inodes. Docker cannot create
runc state files (`docker exec` → `no space left on device`), container JSON
logs corrupt mid-write, and write-heavy paths (Postgres temp, Loki, email
spool) fail. Cascades into “silent” MFA email loss and unhealthy containers.

## First checks
1. Prometheus: `node_filesystem_avail_bytes` / `node_filesystem_size_bytes` by
   `mountpoint` (exclude `tmpfs|overlay`).
2. Inodes: `node_filesystem_files_free / node_filesystem_files` (100% inodes
   with “free” bytes still blocks creates).
3. Host: `df -h`, `df -i`, `docker system df`.
4. Oversized container logs:
   `find /var/lib/docker/containers -name '*-json.log' -size +50M`.
5. Loki/ES/Prometheus retention vs disk size; diagnostic-agent `/app/audit`.

## Common causes
- Unbounded Docker JSON logs without rotation (hundreds of MB per container).
- Accumulated unused ECR image tags on a small root volume (~30G).
- Loki/Prometheus/ES data growth beyond host capacity.
- One-shot tools leaving large images (`ollama`, build caches).

## Blast radius
All containers on the host. First symptoms: `docker exec` failures, 503
healthchecks, corrupt `docker logs` streams, async notification failures,
Alertmanager/agent inability to write state.

## Example log lines (synthetic)
```json
{"@timestamp":"2026-07-24T05:40:00.000Z","level":"ERROR","logger_name":"com.publishi.platform.health.DiskSpaceHealthIndicator","service":"platform-service","trace_id":"6789abcdef01234567890123456789012","message":"Free disk space below threshold: path=/var/lib/docker free=20K total=30G (0%); writes may fail"}
{"@timestamp":"2026-07-24T05:40:12.410Z","level":"ERROR","logger_name":"org.hibernate.engine.jdbc.spi.SqlExceptionHelper","service":"platform-service","trace_id":"789abcdef01234567890123456789013","message":"could not write to file: No space left on device"}
{"@timestamp":"2026-07-24T05:41:00.100Z","level":"ERROR","logger_name":"com.publishi.notification.service.impl.EmailServiceImpl","service":"platform-service","trace_id":"89abcdef01234567890123456789014","message":"Failed to send template email to: user@example.com; nested exception is java.io.IOException: No space left on device"}
```

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
