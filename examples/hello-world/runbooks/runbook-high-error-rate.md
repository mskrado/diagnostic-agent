# Runbook: High error rate on the app service

**Alert:** `rate(http_requests_total{code=~"5.."}[5m])` elevated for `app`.

## Meaning
The application is returning 5xx responses at an unusual rate.

## First checks
1. Loki: `{service="app"} | json | level=~"ERROR|WARN"`
2. Prometheus: `up{service="app"}` and dependency `up` for postgres/redis
3. Recent deploys / config changes

## Common causes
- Downstream database or cache unavailable
- Bug in a recently deployed change
- Resource exhaustion (CPU / memory / file descriptors)

## Blast radius
API gateway and any clients calling `app`.

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
