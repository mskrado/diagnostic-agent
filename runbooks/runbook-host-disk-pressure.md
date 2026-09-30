# Runbook: HostDiskPressure (node filesystem low)

**Alerts:**
- `HostDiskSpaceLow` — `node_filesystem_avail_bytes / size < 0.10` for 10m
- `HostDiskSpaceCritical` — same ratio `< 0.05` for 5m (severity critical)
- `HostDiskFillPredicted` — `predict_linear(...[6h], 24h) < 0` for 1h

## Meaning
The host root volume is exhausting space and/or inodes. On a Docker host the
usual culprit is `/var/lib/docker` (images, container logs, volumes) sharing a
small root volume (~30G). Docker cannot create runc state files (`docker exec`
→ `no space left on device`), container JSON logs corrupt mid-write, and
write-heavy paths (Postgres temp, Loki, email spool) fail.

## First checks (HostDiskSpaceLow / HostDiskFillPredicted)
1. Which mount: `df -h` and `df -i` (inodes 100% blocks creates even with free
   bytes). PromQL: `node_filesystem_avail_bytes{fstype!~"tmpfs|overlay"}`.
2. Is it Docker: `sudo du -xsh /var/lib/docker /var/log /var/cache /home /opt
   2>/dev/null | sort -h`. If `/var/lib/docker` dominates, go to the Docker
   sections below.
3. `docker system df` — the RECLAIMABLE column says how much prune would free.

## Docker image / overlay2 bloat (HostDiskFillPredicted, HostDiskSpaceLow)
Signature: `/var/lib/docker/overlay2` is most of the disk and `docker system
df` shows Images with a large RECLAIMABLE share (e.g. 23G, 78% reclaimable).
Cause: every deploy pulls a new release tag and old tags are never removed;
failed pulls/builds leave dangling `<none>` layers. Confirm:
- `docker system df`
- `docker images --format '{{.Repository}}' | sort | uniq -c | sort -rn`
  (many tags per app repository = release history)
- `docker images -f dangling=true`
- `docker ps --format '{{.Image}}' | sort -u` (tags actually running)

## Docker image cleanup, ranked (hypotheses-only; a human runs these)
1. Low risk: `docker image prune -f` — dangling layers only.
2. High impact: keep the running tag plus 2–3 rollback tags per app repo,
   `docker rmi` the rest deliberately, or `docker image prune -a -f --filter
   "until=168h"` (removes every image not used by any container — including
   rollback tags and stopped-stack images; review first).
3. Secondary: `sudo yum clean all` / `apt-get clean`,
   `sudo journalctl --vacuum-size=100M`.
4. Review before `docker volume prune`: named volumes hold databases,
   dashboards and metrics.

## Do not delete without a decision
- Images of running containers and the current release tag.
- Named volumes (postgres, redis, grafana, loki, prometheus, agent audit).
- Observability stack images, unless that stack is being retired.
- Current release symlink targets under static/web roots.

## Other common causes
- Unbounded Docker JSON logs without rotation:
  `sudo find /var/lib/docker/containers -name '*-json.log' -size +50M`.
- Loki/Prometheus/Elasticsearch data growth beyond retention vs disk size.
- journald without `SystemMaxUse`; package caches (`/var/cache/yum`).
- One-shot tools leaving large images (`ollama`, build cache).

## Prevention
- Post-deploy prune in the deploy path: keep N=2–3 tags per repo, then
  `docker image prune -af --filter "until=168h"`.
- Docker `log-opts` `max-size`/`max-file`; journald `SystemMaxUse=100M`.
- Export `docker system df` to node_exporter (textfile collector) so the
  alert carries image/reclaimable bytes.
- Grow the EBS volume if multi-version JVM images + observability share 30G.

## Blast radius
All containers on the host. First symptoms: `docker exec` failures, 503
healthchecks, corrupt `docker logs` streams, async notification failures,
Alertmanager/agent inability to write state.

## Example log lines (synthetic)
```json
{"@timestamp":"2026-07-24T05:40:00.000Z","level":"ERROR","logger_name":"com.example.platform.health.DiskSpaceHealthIndicator","service":"platform-service","trace_id":"6789abcdef01234567890123456789012","message":"Free disk space below threshold: path=/var/lib/docker free=20K total=30G (0%); writes may fail"}
{"@timestamp":"2026-07-24T05:40:12.410Z","level":"ERROR","logger_name":"org.hibernate.engine.jdbc.spi.SqlExceptionHelper","service":"platform-service","trace_id":"789abcdef01234567890123456789013","message":"could not write to file: No space left on device"}
{"@timestamp":"2026-07-24T05:41:00.100Z","level":"ERROR","logger_name":"com.example.notification.service.impl.EmailServiceImpl","service":"platform-service","trace_id":"89abcdef01234567890123456789014","message":"Failed to send template email to: user@example.com; nested exception is java.io.IOException: No space left on device"}
```

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
