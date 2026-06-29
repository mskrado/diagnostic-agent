# Runbook: ContainerRestartLoop (cAdvisor restarts)

**Alert:** `ContainerRestarting` — `rate(container_start_time_seconds[15m]) > 0` with repeated starts.

## Meaning
A container is crash-looping. Dependent services see connection refused or timeouts.

## First checks
1. Identify container: `container_last_seen` / `container_start_time_seconds` labels.
2. `docker logs <container> --tail 200` on the host.
3. OOM kills: dmesg or `container_memory_failures_total`.

## Common causes
- Misconfiguration after deploy (env var, JDBC URL).
- OOM (Elasticsearch, Ollama, platform-service heap).
- Failed healthcheck causing restart policy loop.

## Blast radius
Depends on container — map name to `service_map.yaml` entry.

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
