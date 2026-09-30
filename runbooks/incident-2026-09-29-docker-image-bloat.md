# Post-mortem: 2026-09-29 — HostDiskFillPredicted from Docker image history

**Impact:** No outage. `HostDiskFillPredicted` fired on the host; root volume
(30G) at 94% and trending to full within 24h. Would have broken `docker exec`,
container logs and DB temp writes once full.

**Root cause:** `/var/lib/docker` used 23G, of which `overlay2` (image layers)
was 22G. `docker system df`: Images 22.96GB, ~18.06GB reclaimable (78%).
Every deploy pulled a new release tag (18 tags each of the app images,
1.0.7 → 1.2.3) and nothing pruned old tags; 20 dangling `<none>` images from
old pulls. Only the current release was running. Inodes were fine (9%).

**Detection:** `HostDiskFillPredicted` (predict_linear). The diagnostic agent
had no host metrics and only the intro of the disk runbook, so it did not name
Docker; a human found it with `du -xsh /var/lib/docker` + `docker system df`.

**Resolution:** `docker image prune -f`, then removed old app tags keeping the
current release plus 2–3 rollback tags. Secondary: `yum clean all` (~0.8G),
`journalctl --vacuum-size=100M` (journal 425M).

**Lessons / prevention:**
- On disk alerts check `/var/lib/docker` and `docker system df` first.
- Add a post-deploy prune (keep N tags per repo).
- Cap journald and Docker json-file logs; consider a larger EBS volume.
- Export Docker disk usage to Prometheus so the alert carries the cause.

**Tags:** host, disk, docker, overlay2, images, prune, HostDiskFillPredicted,
HostDiskSpaceLow
