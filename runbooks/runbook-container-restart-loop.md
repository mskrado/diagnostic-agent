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

## Example log lines (synthetic)
```json
{"@timestamp":"2026-07-20T20:14:18.000Z","level":"ERROR","logger_name":"org.springframework.boot.web.embedded.tomcat.TomcatWebServer","service":"platform-service","trace_id":"789abcdef012345678901234567890123","message":"Tomcat failed to start: Address already in use; nested exception is java.net.BindException"}
{"@timestamp":"2026-07-20T20:14:25.500Z","level":"ERROR","logger_name":"org.springframework.boot.SpringApplication","service":"platform-service","trace_id":"89abcdef0123456789012345678901234","message":"Application run failed; Failed to configure a DataSource: 'url' attribute is not specified — check JDBC env vars after deploy"}
```
Container-level OOM kills show up in cAdvisor (`container_memory_failures_total`)
and host `dmesg`, not always in the app log — pair metrics with these lines.

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
