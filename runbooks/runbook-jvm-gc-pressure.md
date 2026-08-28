# Runbook: JvmGcPressure (GC pauses / heap)

**Alert:** `JvmGcPauseHigh` or `JvmHeapUsageHigh` on `platform-service` / `api-gateway`.

## Meaning
JVM is spending excessive time in GC or nearing heap limits. Latency spikes and
503s may follow even without high error rates.

## First checks
1. `jvm_gc_pause_seconds_sum` / `jvm_memory_used_bytes{area="heap"}`.
2. Thread pool saturation: `http_server_requests_seconds` p99 vs pool metrics.
3. Recent deploy changing heap `-Xmx` or traffic pattern.

## Common causes
- Heap too small for workload.
- Memory leak in a module (growing old-gen).
- Large payload caching without bounds.

## Blast radius
Affected JVM service; gateway may propagate latency to all routes.

## Example log lines (synthetic)
```json
{"@timestamp":"2026-07-20T20:10:05.001Z","level":"WARN","logger_name":"com.example.platform.health.JvmHealthIndicator","service":"platform-service","trace_id":"89abcdef0123456789abcdef012345678","message":"High GC pause detected: young=812ms old=2410ms; heap used=1784MB/2048MB (87%); allocation rate elevated"}
{"@timestamp":"2026-07-20T20:10:18.440Z","level":"ERROR","logger_name":"org.springframework.boot.SpringApplication","service":"platform-service","trace_id":"9abcdef0123456789abcdef0123456789","message":"Application run failed; nested exception is java.lang.OutOfMemoryError: Java heap space"}
{"@timestamp":"2026-07-20T20:10:50.055Z","level":"WARN","logger_name":"org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor","service":"platform-service","trace_id":"bcdef0123456789abcdef012345678901","message":"Task rejected from ThreadPoolExecutor[Running, pool size = 50, active threads = 50, queued tasks = 200]"}
```

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
