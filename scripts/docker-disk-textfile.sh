#!/usr/bin/env bash
# Export Docker disk usage (`docker system df`) as Prometheus metrics via the
# node_exporter textfile collector, so host disk alerts carry the Docker share.
#
# Runs on the Docker HOST (root or docker group + write access to the textfile
# dir), e.g. from cron every 15 minutes:
#   */15 * * * * root /opt/diagnostic-agent/scripts/docker-disk-textfile.sh
# node_exporter must run with --collector.textfile.directory=<same dir>.
#
# Env:
#   TEXTFILE_DIR   default /var/lib/node_exporter/textfile_collector
#   DOCKER_ROOT    default /var/lib/docker (container json log sizing)
#   DOCKER_BIN     default docker
set -euo pipefail

TEXTFILE_DIR="${TEXTFILE_DIR:-/var/lib/node_exporter/textfile_collector}"
DOCKER_ROOT="${DOCKER_ROOT:-/var/lib/docker}"
DOCKER_BIN="${DOCKER_BIN:-docker}"
OUT="${TEXTFILE_DIR}/docker_disk.prom"

mkdir -p "$TEXTFILE_DIR"
tmp="$(mktemp "${OUT}.XXXXXX")"
trap 'rm -f "$tmp"' EXIT

df_rows="$("$DOCKER_BIN" system df --format '{{.Type}}\t{{.TotalCount}}\t{{.Size}}\t{{.Reclaimable}}')"

dangling="$("$DOCKER_BIN" images -f dangling=true -q | wc -l | tr -d ' ')"
max_tags="$("$DOCKER_BIN" images --format '{{.Repository}}' \
  | { grep -v '^<none>$' || true; } | sort | uniq -c | sort -rn | awk 'NR==1{print $1}')"
max_tags="${max_tags:-0}"

log_bytes=0
if [ -d "${DOCKER_ROOT}/containers" ] && [ -r "${DOCKER_ROOT}/containers" ]; then
  log_bytes="$(find "${DOCKER_ROOT}/containers" -name '*-json.log' -printf '%s\n' 2>/dev/null \
    | awk '{s+=$1} END{printf "%.0f", s}')"
fi

{
  echo "# HELP docker_disk_bytes Docker disk usage by type (docker system df SIZE)."
  echo "# TYPE docker_disk_bytes gauge"
  echo "# HELP docker_disk_reclaimable_bytes Bytes docker prune could free by type (RECLAIMABLE)."
  echo "# TYPE docker_disk_reclaimable_bytes gauge"
  echo "# HELP docker_disk_objects Object count by type (TOTAL)."
  echo "# TYPE docker_disk_objects gauge"
  # docker prints decimal units (kB/MB/GB, 1000-based); Reclaimable adds " (78%)".
  printf '%s\n' "$df_rows" | awk -F'\t' '
    function bytes(s,   n, u, m) {
      sub(/ .*/, "", s)
      n = s; sub(/[A-Za-z]+$/, "", n)
      u = substr(s, length(n) + 1)
      m = 1
      if (u == "kB" || u == "KB") m = 1e3
      else if (u == "MB") m = 1e6
      else if (u == "GB") m = 1e9
      else if (u == "TB") m = 1e12
      return n * m
    }
    NF >= 4 {
      t = tolower($1); gsub(/ +/, "_", t)
      printf "docker_disk_bytes{type=\"%s\"} %.0f\n", t, bytes($3)
      printf "docker_disk_reclaimable_bytes{type=\"%s\"} %.0f\n", t, bytes($4)
      printf "docker_disk_objects{type=\"%s\"} %d\n", t, $2
    }'
  echo "# HELP docker_images_dangling Dangling (<none>) images."
  echo "# TYPE docker_images_dangling gauge"
  echo "docker_images_dangling ${dangling}"
  echo "# HELP docker_image_tags_max_per_repo Most tags kept for a single image repository (release history)."
  echo "# TYPE docker_image_tags_max_per_repo gauge"
  echo "docker_image_tags_max_per_repo ${max_tags}"
  echo "# HELP docker_container_logs_bytes Total size of container json-file logs."
  echo "# TYPE docker_container_logs_bytes gauge"
  echo "docker_container_logs_bytes ${log_bytes}"
  echo "# HELP docker_disk_collector_last_run_timestamp_seconds Last successful run."
  echo "# TYPE docker_disk_collector_last_run_timestamp_seconds gauge"
  echo "docker_disk_collector_last_run_timestamp_seconds $(date +%s)"
} > "$tmp"

chmod 0644 "$tmp"
mv "$tmp" "$OUT"
trap - EXIT
