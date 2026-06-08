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

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
