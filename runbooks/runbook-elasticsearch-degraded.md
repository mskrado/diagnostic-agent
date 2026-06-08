# Runbook: ElasticsearchDegraded (search index unhealthy)

**Alert:** `ElasticsearchLogErrors` — Loki ERROR logs matching `elasticsearch|RestClient` on platform-service.

## Meaning
The `search` module cannot index or query. Search APIs and content discovery degrade;
writes may still succeed in Postgres.

## First checks
1. Logs: `{service="platform-service"} | json | logger_name=~".*search.*" | level="ERROR"`.
2. ES cluster health (if exporter available): `elasticsearch_cluster_health_status`.
3. Disk on ES container host: `node_filesystem_avail_bytes` for ES data volume.

## Common causes
- Cluster yellow/red — shard unassigned, single-node overload.
- Authentication failure (`ELASTIC_PASSWORD` mismatch).
- Disk watermark exceeded — read-only index blocks.

## Blast radius
`search` module; content listing/search endpoints; RAG indexing jobs if any.

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
