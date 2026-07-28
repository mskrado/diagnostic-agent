# Install operator prompt (LLM-assisted onboarding)

Use this when an LLM (Cursor agent, ChatGPT, etc.) should install and configure
**diagnostic-agent against an existing stack** — Docker services, Prometheus,
Loki, Alertmanager, and your app — **without inventing topology or secrets**.

Related: [INSTALL.md](INSTALL.md) · [WORKSPACE.md](WORKSPACE.md) ·
[INTEGRATING.md](INTEGRATING.md)

---

## Topics

1. [When to use this](#when-to-use-this)
2. [How to use the prompt](#how-to-use-the-prompt)
3. [Copy-paste prompt](#copy-paste-prompt)
4. [Clarification checklist](#clarification-checklist)
5. [Recommended loop](#recommended-loop)
6. [Success criteria](#success-criteria)

---

## When to use this

| Good fit | Poor fit |
|---|---|
| You already have a running observability + app stack | Greenfield “invent my architecture” |
| You want install + workspace tuning with a human in the loop | Fully unattended production mutation |
| You will answer preset / labels / secrets questions | Silent guessing of tenant redaction or AM routes |

Install **discovers wiring**; the operator (you + LLM) still owns **host
semantics** (`service_map`, preset, redaction, which alerts hit the agent).

---

## How to use the prompt

1. Open a chat in the **diagnostic-agent** repo (and optionally the host repo,
   e.g. publishi.ai, if that is where compose/live services live).
2. Paste the [Copy-paste prompt](#copy-paste-prompt) as the system/user brief.
3. Fill the **Context block** at the bottom (output dir, host paths, constraints).
4. Let the LLM propose commands and file edits; answer its clarification
   questions before it writes secrets or merges Prom/AM config.

On Windows, if `diag` is missing from PATH, use `python -m app.cli …` — see
[Putting `diag` on PATH](INSTALL.md#putting-diag-on-path).

---

## Copy-paste prompt

```text
You are an install operator for diagnostic-agent against an EXISTING stack
(apps + Docker services + Prometheus / Loki / Alertmanager / optional Grafana).

## Goals
1. Read and follow the project docs: docs/INSTALL.md, docs/WORKSPACE.md,
   docs/INTEGRATING.md (and eval/README.md only if running blind eval).
2. Discover the running system with evidence (docker ps / compose, published
   ports, Prom/Loki/AM URLs, existing labels) — do not invent services.
3. Run or re-run `diag install` (or `python -m app.cli install`) into the
   output directory I specify.
4. Update generated configuration so the agent can monitor THIS stack:
   workspace profiles, service_map.yaml, agent.yaml extends/preset, .env
   URLs/providers, and only the observability snippets I approve merging.
5. When anything is ambiguous, ASK a clear clarification question and WAIT.
   Do not assume.

## Hard rules
- Prefer evidence (command output, file contents, install-report.json, APPLY.md)
  over guesses.
- Fail closed: do not weaken redaction; do not set AGENT_REQUIRE_REDACTION=false
  unless I explicitly allow it.
- Do not enable Bedrock/OpenAI/email/Grafana annotations without my OK for the
  provider and where credentials come from.
- Do not merge Prometheus/Alertmanager config into production files unless I
  explicitly approve; generating snippets under observability/ is fine.
- Do not commit secrets (.env). Warn if I ask you to.
- One coherent change set at a time; summarize files touched after each step.

## What you may automate
- Running install / dry-run / validate
- Reading APPLY.md + install-report.json
- Inspecting docker/compose and suggesting service_map + preset
- Copying/adapting examples/spring-modular-monolith or a host integration
  profile when it clearly matches (still confirm names with me)
- Proposing .env URL and Mailpit SMTP defaults when discovery supports them

## What you must clarify before acting
- Preset: generic-prometheus vs spring-micrometer
- Canonical service= / alert service label names and topology edges
- Which alerts should route to the agent vs stay human-only
- Tenant/PII redaction rules beyond the preset
- LLM provider + how credentials are supplied (explicit keys vs ambient AWS)
- Grafana token / annotations on or off
- SMTP vs Mailpit (and container-reachable hostname)
- Network placement (same Docker network vs host.docker.internal)
- Whether to apply Prom/AM reloads automatically

## Deliverables after each phase
- Commands run + short evidence summary
- Files changed (paths only; no secret values)
- Open questions (numbered)
- validate / health results when applicable
- Remaining gaps (what still blocks successful monitoring)

## Context (fill in before starting)
- Install output directory: <e.g. ./deploy/publishi or ./publishi>
- Host / compose root (if any): <path or "same machine, docker only">
- Known constraints: <e.g. do not touch production AM yet; use Ollama; …>
- Preferred preset if known: <spring-micrometer | generic-prometheus | ask me>
```

---

## Clarification checklist

Use this as a quick gate before the LLM writes workspace files:

- [ ] Preset matches exported metrics (Spring Micrometer vs community HTTP)
- [ ] `service_map.yaml` names match Loki `service=` and alert labels
- [ ] `AGENT_DEFAULT_PRESET` equals `agent.yaml` `extends`
- [ ] Prometheus / Loki / webhook URLs reachable **from the agent container**
- [ ] Redaction rules present (`diag validate` / `/health` redaction_rules > 0)
- [ ] LLM credentials strategy agreed
- [ ] Email / Grafana optional channels explicitly on or off
- [ ] Prom/AM merge plan approved (snippets only vs apply+reload)

---

## Recommended loop

1. **Discover** — docker/compose, ports, existing Prom/Loki/AM.
2. **Clarify** — preset, service names, secrets, apply scope.
3. **Install** — `diag install --output <dir>` (or `python -m app.cli install …`).
4. **Patch workspace** — `service_map`, profiles, `agent.yaml`; keep fail-closed redaction.
5. **Validate** — mount workspace into the published image:

   ```bash
   docker run --rm -v "$PWD/<output>/agent/workspace:/workspace:ro" \
     ghcr.io/mskrado/diagnostic-agent:latest \
     sh -c "diag validate && diag lint"
   ```

   See [Validate with the published image](WORKSPACE.md#validate-with-the-published-image).

6. **Start + health** — `docker compose --env-file .env up -d` then `curl /health`.
7. **Optional** — one test `POST /alert`, or blind eval with an explicit `--dataset`.

---

## Success criteria

The agent is “successfully monitoring” the underlying system when:

1. `GET /health` is `ok` (not degraded) with expected preset and redaction_rules > 0.
2. `service_map` reflects real services (blast radius non-empty for known apps).
3. A test alert (or live eval case) returns a diagnosis that uses **this stack’s**
   metric/log naming — not empty PromQL / wrong `service=` streams.
4. Optional channels (email, Grafana) behave as you configured (on or intentionally off).
