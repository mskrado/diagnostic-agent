# Runbook: RedisConnectionErrors (cache/session failures)

**Alert:** `RedisLogErrors` — Loki rate of `{service="platform-service"} |= "redis" |= "timeout|connection"` > 0 for 5m.

## Meaning
Lettuce client cannot reach Redis (cache + session store). Auth sessions, rate
limits, and module caches may fail; platform-service may return 5xx.

## First checks
1. Logs: `{service="platform-service"} | json | level="ERROR" |~ "redis|Lettuce"`.
2. Redis container health: `docker ps` / cAdvisor `container_last_seen{container=~".*redis.*"}`.
3. Pool saturation on platform-service (secondary): `hikaricp_connections_pending`.

## Common causes
- Redis container OOM/restart — memory limit or eviction storm.
- Wrong `REDIS_PASSWORD` after deploy.
- Network partition between app and redis on compose network.

## Blast radius
`auth` (sessions), any module using `@Cacheable`, notification throttles.

## Example log lines (synthetic)
```json
{"@timestamp":"2026-07-20T20:04:10.100Z","level":"ERROR","logger_name":"io.lettuce.core.RedisException","service":"platform-service","trace_id":"f6789012345678abcdef0123456789ab","message":"Unable to connect to Redis; nested exception is io.lettuce.core.RedisConnectionException: Unable to connect to redis:6379"}
{"@timestamp":"2026-07-20T20:04:22.333Z","level":"ERROR","logger_name":"io.lettuce.core.protocol.CommandHandler","service":"platform-service","trace_id":"6789012345678abcdef0123456789abc","message":"io.lettuce.core.RedisCommandTimeoutException: Command timed out after 5 second(s)"}
{"@timestamp":"2026-07-20T20:04:40.512Z","level":"ERROR","logger_name":"org.springframework.data.redis.RedisConnectionFailureException","service":"platform-service","trace_id":"789012345678abcdef0123456789abcd","message":"Unable to connect to Redis; nested exception is io.lettuce.core.RedisConnectionException: NOAUTH Authentication required."}
```

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
