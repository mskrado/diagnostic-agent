# Diagnostic agent runbooks

Markdown corpus for RAG (`AGENT_RAG_ENABLED=true`). The agent chunks files at
**800 characters / 80 overlap** and retrieves **top_k=3** chunks per alert.

## Layout

| File | Purpose |
|---|---|
| `_TEMPLATE-runbook.md` | Scaffold for new alert runbooks |
| `_TEMPLATE-postmortem.md` | Scaffold for incident write-ups |
| `runbook-*.md` | Alert-specific playbooks (hypotheses only) |
| `incident-*.md` | Past incidents (ground truth for similar failures) |

## Authoring

1. Copy `_TEMPLATE-runbook.md` → `runbook-<alert-or-component>.md`.
2. Reference alert names from `docs/observability/ALERTING_STRATEGY.md`.
3. Include PromQL/Loki queries operators can paste into Grafana.
4. Keep **Hypotheses-only** — no auto-remediation steps.
5. After a real incident, add `incident-YYYY-MM-DD-<slug>.md` from the postmortem template.

Rebuild the vector store by restarting `diagnostic-agent` (Chroma persists under `/app/chroma_db`).
