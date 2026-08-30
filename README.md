# diagnostic-agent

**When the pager fires at 3 a.m., you should already have a hypothesis — not a
blank Slack thread.**

diagnostic-agent sits next to Prometheus and Alertmanager. An alert comes in;
the agent pulls **metrics**, **logs**, and your **service map**, retrieves the
runbooks that actually match, reasons with an LLM you choose, and delivers a
structured report: what failed, what the evidence says, who is in the blast
radius, and the next commands a human should run.

**Hypotheses only, by default.** It does not restart pods, flip feature flags,
or "fix" production. Opt-in sandboxed execution exists for hosts that want it —
off unless you turn it on.

```
alert → metrics + logs + runbooks → reasoned report → you decide
```

[Install it](docs/INSTALL.md) · [Add a runbook](#join-the-project) ·
[Apache-2.0](LICENSE)

---

## The 3 a.m. problem

On-call already has dashboards. What they do **not** have is a first pass that:

- looks at **this** alert against **this** stack's PromQL and log labels
- reads **your** runbooks, not a generic chatbot's memory
- redacts tenant IDs and secrets **before** anything is written down
- says "I am not sure" when the evidence is thin

That is the gap this project fills. It is config-driven, one agent per stack,
and built so a private fork can own the workspace while upstream ships the
engine.

---

## A report you actually get

This is the shape of a live diagnosis — same fields the agent emails, posts to
Slack, and writes to the audit log. The numbers and log lines come from the
shipped [Hikari pool eval case](eval/blind_eval_dataset.yaml); a real run on
your stack fills them from Prometheus and Loki.

```text
Diagnostic report
=================
Alert:     HikariPoolExhaustion
Service:   platform-service
Severity:  critical
Confidence: high

Primary hypothesis (88%)
  HikariCP is saturated: 20/20 connections active, 47 threads waiting.
  The database is reachable — nothing is being handed out.

Evidence
  metric  platform-service.db_pool_pending = 47
  log     HikariPool-1 - Connection is not available, request timed out
          after 30000ms (total=20, active=20, idle=0, waiting=47)
  log     Unable to acquire JDBC Connection; SQLTransientConnectionException
  runbook runbook-db-pool-exhaustion.md  (RAG hit)

Issue categories
  database-pool   88%  pool exhausted (active == max)
  api             61%  elevated 5xx is a symptom of the pool, not the cause

Blast radius
  platform-service → api-gateway → clients waiting on checkouts
  postgres itself is healthy; cache/search not implicated

Suggested next steps
  1. Confirm pool vs. database: is postgres accepting connections?
  2. Who holds the 20 connections — leak, long transaction, or undersized max?

Tool run examples (copy-paste; you run them)
  curl -s "$PROM/api/v1/query?query=hikaricp_connections_active{service=\"platform-service\"}"
  curl -s "$PROM/api/v1/query?query=hikaricp_connections_pending{service=\"platform-service\"}"
  logcli query '{service="platform-service"} |~ "(?i)connection is not available|waiting="' --limit 20

Fix suggestions (human-run; the agent does not auto-remediate)
  - Find and kill the stuck transaction or leaking caller
  - Raise hikari.maximum-pool-size only after you know why 20 is not enough
  - Add a slow-query / checkout-timeout alert so this pages earlier next time

Hypotheses + guidance only — no auto-remediation.
```

A weak or empty workspace does not silently invent a cause. Confidence drops,
redaction is fail-closed, and the report says the data is insufficient.

---

## Why people deploy it

| | |
|---|---|
| **Your stack, not a demo** | PromQL templates, Loki labels, topology, and prompt hints live in a [workspace](docs/WORKSPACE.md) you own. Presets cover generic Prometheus and Spring Micrometer; you overlay the rest. |
| **Your runbooks, retrieved** | Markdown playbooks are indexed (RAG) and pulled in per alert. The preferred contribution to this repo is a runbook + eval case + scenario. |
| **Evidence or it did not happen** | Every claim has to cite a metric value or a log line the agent was given. Blind eval scores that habit. |
| **Secrets stay in the stack** | Redaction rules accumulate along the preset chain. Zero rules → the process **refuses to start**. |
| **One agent, one stack** | Credentials, the RAG index, and the service map are scoped to one workspace. A mis-pointed mount cannot leak another team's runbooks. |
| **You pick the model** | Ollama, Bedrock, OpenAI, Anthropic, Gemini — same graph. Online hosts pull the image; air-gapped hosts build from the fork. |

Routing (report / escalate / execute) and sandboxed runbook actions are
**opt-in** and fail-closed. Defaults never auto-remediate.
Details: [sandboxed execution](docs/design/sandboxed-execution.md).

---

## How a diagnosis runs

```
Prometheus ──▶ Alertmanager ──▶ POST /alert
                                   │
                     detect → retrieve → rag_lookup → correlate → report
                                                                   │
                                              audit JSONL · email · Slack ·
                                              PagerDuty · Grafana annotation
```

1. **Detect** — resolve the service and alert type from labels you already emit.
2. **Retrieve** — PromQL from your metrics profile; LogQL from your logs profile;
   blast radius from `service_map.yaml`.
3. **RAG** — fetch the runbooks that match, if any.
4. **Correlate** — the LLM returns structured JSON (categories, confidence,
   copy-pasteable commands). It does not run those commands.
5. **Deliver** — redacted report to the channels you enabled.

Install, start, and upgrade: **[docs/INSTALL.md](docs/INSTALL.md)**.

---

## Get it running

The supported path is a **private copy** of this repo. `diag init` discovers
your Prometheus / Loki / Alertmanager and scaffolds `client/`.

```bash
./scripts/bootstrap-venv.sh && source .venv/bin/activate
diag init
cp client/agent/.env.example client/agent/.env   # fill secrets
./client/scripts/start.sh
curl -sf http://127.0.0.1:8001/health
```

Want to feel the loop before wiring a stack? The bundled example is enough:

```bash
pip install -e ".[dev]"
diag validate -w examples/hello-world
diag serve -w examples/hello-world --port 8000
```

```bash
curl -X POST http://localhost:8000/alert -H 'Content-Type: application/json' -d '{
  "alerts": [{
    "status": "firing",
    "labels": {"alertname": "HighErrorRate", "service": "app", "severity": "warning"}
  }]
}'
```

Full lifecycle (mirror-clone, Amazon Linux, air gap, upgrades, throwaway
bundles): **[docs/INSTALL.md](docs/INSTALL.md)**.

---

## Join the project

This is an open Apache-2.0 project. The highest-leverage contribution is
**making the agent smarter about a real failure mode** — not a framework rewrite.

To add a diagnostic capability, send **all three**:

1. A runbook under [`runbooks/`](runbooks/) (start from
   [`runbooks/_TEMPLATE-runbook.md`](runbooks/_TEMPLATE-runbook.md))
2. A case in [`eval/blind_eval_dataset.yaml`](eval/blind_eval_dataset.yaml)
   (synthetic logs + ground truth — no real PII)
3. A scenario in `runbook_scenarios.yaml` (matching alert labels)

CI lints the corpus on every PR **without LLM credentials**: schema, runbook ↔
scenario pairing, grounding tokens, hypotheses-only wording. Optional model eval
is maintainer-triggered.

Other ways in:

- Improve a [preset](docs/WORKSPACE.md) or the Spring example under
  [`examples/spring-modular-monolith/`](examples/spring-modular-monolith/)
- Tighten redaction, delivery, or the install generator
- File a [bug](https://github.com/mskrado/diagnostic-agent/issues/new?template=bug_report.yml)
  or a gap you hit on-call

Read **[CONTRIBUTING.md](CONTRIBUTING.md)** and
**[docs/SDLC_GUIDE.md](docs/SDLC_GUIDE.md)** (issue → `feature/<slug>-<n>` →
`devel` → release). Every commit is DCO-signed (`git commit -s`).

```bash
python -m venv .venv && pip install -e ".[dev]"
pytest -q
diag lint
```

---

## Docs

| | |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | Install, upgrade, runtimes, throwaway bundles |
| [docs/WORKSPACE.md](docs/WORKSPACE.md) | Workspace files, presets, precedence |
| [docs/INTEGRATING.md](docs/INTEGRATING.md) | Manual wiring if you skip the generator |
| [docs/TESTING.md](docs/TESTING.md) · [TESTING_STRATEGY.md](docs/TESTING_STRATEGY.md) | Operator E2E and the ten test layers |
| [runbooks/README.md](runbooks/README.md) | Authoring the RAG corpus |
| [eval/README.md](eval/README.md) | Blind eval and routing replay |
| [SECURITY.md](SECURITY.md) | Supported versions and reporting |

Architecture notes (routing, execution, threat model) live in
[docs/design/sandboxed-execution.md](docs/design/sandboxed-execution.md).
Every `AGENT_*` knob is listed in [`.env.example`](.env.example).

---

## License

[Apache-2.0](LICENSE). Use it, fork it, ship it next to your stack.
