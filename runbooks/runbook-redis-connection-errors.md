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

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
