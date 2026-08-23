# Diagnostic agent runbooks

Markdown corpus for RAG (`AGENT_RAG_ENABLED=true`). The agent chunks files at
**800 characters / 80 overlap**. Retrieval is **per distinct error family** in
the log sample (`AGENT_RAG_TOP_K` chunks each, capped by `AGENT_RAG_MAX_CHUNKS`)
so mixed incidents can surface postgres + redis + JVM runbooks together.

---

## Topics

1. [Layout](#layout)
2. [Authoring](#authoring)
3. [Executable steps (`runbook-actions`)](#executable-steps-runbook-actions)

---

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
4. Keep the prose **hypotheses-only** — describe what a human should check, not
   commands the agent claims to have run. Anything the agent may actually
   execute goes in a [`runbook-actions` block](#executable-steps-runbook-actions),
   never in prose.
5. After a real incident, add `incident-YYYY-MM-DD-<slug>.md` from the postmortem template.

Rebuild the vector store by restarting `diagnostic-agent` (Chroma persists under `/app/chroma_db`).

## Executable steps (`runbook-actions`)

A runbook is **advisory-only** unless it carries a fenced `runbook-actions`
block. Every runbook in this corpus is advisory-only today; adding a block is
how a host opts one runbook into
[sandboxed execution](../docs/design/sandboxed-execution.md).

~~~markdown
## Automated actions

```runbook-actions
version: 1
match:
  alert_type: ["RedisConnectionErrors"]
  service: ["platform-service"]
  min_confidence: high
steps:
  - action_id: clear-cdn-cache
```
~~~

| Key | Meaning |
|---|---|
| `match.alert_type` | Alert names this runbook may act on. Omit or leave empty to match any |
| `match.service` | Services it may act on. Omit or leave empty to match any |
| `match.min_confidence` | Minimum LLM confidence — `low`, `medium`, or `high` (default `high`) |
| `steps[].action_id` | Must name an action in [`execution_profile.yaml`](../docs/WORKSPACE.md#execution_profileyaml-optional) |

How a block is treated:

- A runbook whose steps reference an unknown `action_id` is dropped back to
  advisory-only — the agent will not partially execute it.
- Selection requires **exactly one** matching runbook. Zero or several matches
  escalate to a human instead of guessing.
- Only the first step runs today; multi-step execution and post-execution
  verification are not implemented yet.
- Nothing runs at all unless the host sets both `AGENT_ROUTING_ENABLED=true` and
  `AGENT_EXEC_ENABLED=true`, and the named action passes the destructive-action
  classifier.
