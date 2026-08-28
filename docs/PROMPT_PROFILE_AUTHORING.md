# Coding-agent playbook: author `prompt_profile.yaml`

Use this document as the **system / task prompt** for a coding agent that must
build or improve a host `prompt_profile.yaml`. The file frames the diagnostic
LLM (`platform_description` + `tool_run_hints`); it does **not** override core
safety rules in `app/graph/prompts.py`.

---

## Goal

Produce a host `prompt_profile.yaml` that makes `tool_run_examples` and
`fix_suggestions` **copy-pasteable on the real stack**, so the model stops
inventing compose service names, container names, ports, or Prom/Loki labels.

## Non-goals

- Do not change the JSON diagnosis schema or core prompt invariants.
- Do not put secrets, passwords, or live credentials in the profile.
- Do not claim the agent already ran commands — hints are for **human** steps.
- Do not replace `service_map.yaml`; keep names consistent with it.

---

## Inputs the agent must collect (inventory)

Run these against the **same environment** the diagnostic agent will use
(prod EC2, staging, or local compose). Record raw evidence in the PR/issue.

### 1. Mount / workspace layout

```bash
# Where is AGENT_WORKSPACE / agent.yaml?
docker inspect <agent-container> --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'
# Confirm profile path (often /workspace/profile/prompt_profile.yaml)
```

### 2. Three naming planes (never conflate)

| Plane | Where it appears | How to discover |
|---|---|---|
| **Alert / Prom / Loki `service=`** | AM labels, PromQL, LogQL | Live AM payload; `prometheus` label values; Loki `{service="…"}` |
| **Compose service key** | `docker compose … <name>` | `docker compose config --services` |
| **Container name** | `docker logs` / `docker exec` | `docker ps --format '{{.Names}}'` |

Also record **in-network DNS** names the agent container can resolve
(`prometheus`, `loki`, `acme-platform`, …) vs **host localhost ports**.

### 3. Ports and health endpoints

For each app tier: published host port, container listen port, actuator/health
URL. Prefer commands that work **from the agent network** and note host
equivalents separately.

### 4. Measurement gaps

Note missing series, wrong alert labels, or empty cAdvisor `name` labels so the
profile can tell the LLM what **not** to trust. Prefer evidence from
`service_map.yaml` comments and live Prom/Loki checks.

### 5. Failure modes already seen

If audits show invented names (e.g. `acme-platform-service`, wrong
`docker compose logs …`), add an explicit **FORBIDDEN** list in
`tool_run_hints`.

---

## Output schema

```yaml
extends: <preset>   # e.g. generic-prometheus or spring-micrometer parent

platform_description: >-
  One dense paragraph: product name, architecture (gateway / monolith /
  modules), backing stores, observability endpoints, and hard measurement gaps.

tool_run_hints: |-
  Short rules + allowlists + 8–15 golden copy-paste commands.
```

Keep `platform_description` under ~1–2k characters when possible (small models
truncate). Put the naming matrix and forbidden patterns in `tool_run_hints`.

---

## Required sections inside `tool_run_hints`

Write them in this order:

1. **Where to run commands** — in-network DNS vs host localhost.
2. **Allowlist tables** — alert `service=` ↔ compose key ↔ `container_name`.
3. **Hard rules** — use alert `service=` for Prom/Loki filters; never invent
   prefixes; never mix compose keys with `<prefix>-` container prefixes.
4. **Golden commands** — PromQL, LogQL, `docker logs` / `docker compose logs`,
   actuator, dependency probes (`pg_isready`, `redis-cli ping`).
5. **Forbidden examples** — 2–4 wrong strings the model has invented before.
6. **Remediation honesty** — suggest restarts only as operator steps; never
   claim execution.

Every golden command must use names from the allowlist only.

---

## Validation checklist

- [ ] Every compose/container name appears in `docker compose config` / `docker ps`.
- [ ] Every Prom/Loki `service=` filter matches live label values (or
      `service_map` `log_services` / `log_selector`).
- [ ] No hybrid names (`<prefix>-` + compose key glued together).
- [ ] Hints agree with `service_map.yaml` and runbook examples (scrub runbooks
      if they contradict).
- [ ] `diag validate` (or container equivalent) still passes.
- [ ] Spot-check: feed a sample alert; confirm suggested tools use allowlisted
      names only.

---

## Workflow for the coding agent

1. Inventory (section above) → write evidence bullets.
2. Draft `platform_description` from architecture + gaps.
3. Draft `tool_run_hints` with allowlist + golden + forbidden.
4. Diff against the previous profile; prefer additive clarity over rewriting
   unrelated prose.
5. Update the **host** workspace that is actually mounted in prod (not only a
   gitignored mirror).
6. Open/update the host PR; note that the agent container must pick up the
   new file (restart or remount if needed).

---

## Prompt you can paste to a coding agent

```text
You are improving prompt_profile.yaml for a diagnostic-agent host workspace.

Follow docs/PROMPT_PROFILE_AUTHORING.md exactly:
1. Inventory alert/Prom/Loki service labels, compose service keys, and
   docker container names from the live stack (or checked-in compose files).
2. Build platform_description (architecture + measurement gaps).
3. Build tool_run_hints with: run context, allowlist matrix, hard rules,
   golden curl/docker commands, forbidden invented names, no false execution claims.
4. Keep names consistent with service_map.yaml; fix contradictions.
5. Do not put secrets in the file.
6. Validate with the checklist in that doc.

Output the full prompt_profile.yaml and a short evidence note listing sources
(compose paths, docker ps, sample Prom/Loki labels).
```
