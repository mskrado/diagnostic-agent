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

## Example log lines (synthetic)
```json
{"@timestamp":"2026-07-20T20:06:11.200Z","level":"ERROR","logger_name":"org.elasticsearch.client.RestClient","service":"platform-service","trace_id":"9012345678abcdef0123456789abcdef","message":"Request failed: [http_host_connect] Connection refused to elasticsearch:9200"}
{"@timestamp":"2026-07-20T20:06:28.441Z","level":"ERROR","logger_name":"co.elastic.clients.elasticsearch.ElasticsearchException","service":"platform-service","trace_id":"0123456789abcdef0123456789abcdef0","message":"Elasticsearch exception [type=circuit_breaking_exception, reason=[parent] Data too large, would be [1050mb], larger than limit of [1024mb]]"}
{"@timestamp":"2026-07-20T20:06:45.090Z","level":"ERROR","logger_name":"com.publishi.platform.search.SearchService","service":"platform-service","trace_id":"123456789abcdef0123456789abcdef01","message":"Elasticsearch search failed: index_not_found_exception: no such index [content-v3]"}
{"@timestamp":"2026-07-20T20:07:02.777Z","level":"ERROR","logger_name":"org.elasticsearch.client.RestHighLevelClient","service":"platform-service","trace_id":"23456789abcdef0123456789abcdef012","message":"listener timeout after waiting for [30000] ms; bulk index rejected: es_rejected_execution_exception"}
```

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
