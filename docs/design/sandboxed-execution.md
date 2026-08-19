# Design Spec: Sandboxed Runbook Execution (Track B)

| | |
|---|---|
| **Status** | Draft — for review |
| **Gate issue** | [#54](https://github.com/mskrado/diagnostic-agent/issues/54) |
| **Build issues** | [#50](https://github.com/mskrado/diagnostic-agent/issues/50) runner · [#51](https://github.com/mskrado/diagnostic-agent/issues/51) classifier · [#52](https://github.com/mskrado/diagnostic-agent/issues/52) execute node · [#53](https://github.com/mskrado/diagnostic-agent/issues/53) verification loop |
| **Epic** | [#55](https://github.com/mskrado/diagnostic-agent/issues/55) |
| **Supersedes invariant** | Partial, opt-in relaxation of the read-only guarantee (see §2) |

This document is the **gate** for all Track B build work. Issues #50–#53 stay `status:needs-spec`
until this spec is merged and reviewer-approved, and until the routing eval harness
([#48](https://github.com/mskrado/diagnostic-agent/issues/48)) is green.

---

## 0. How to use this spec set (read first)

This overview is the **why** (threat model, architecture, invariants, gating). Each build issue has a
**separate, explicit, near-copy-paste implementation spec** — work from that file, not from this one:

| Issue | Implementation spec | Build order |
|---|---|---|
| [#50](https://github.com/mskrado/diagnostic-agent/issues/50) Sandbox runner | [`exec/50-sandbox-runner.md`](exec/50-sandbox-runner.md) | **1st** — establishes the shared execution foundation (config, profile model, `app/execution/` package). |
| [#51](https://github.com/mskrado/diagnostic-agent/issues/51) Destructive classifier | [`exec/51-destructive-classifier.md`](exec/51-destructive-classifier.md) | **2nd** — needs #50's `AllowlistedAction` model. |
| [#52](https://github.com/mskrado/diagnostic-agent/issues/52) execute node + parsing | [`exec/52-runbook-execute-node.md`](exec/52-runbook-execute-node.md) | **3rd** — needs #50, #51, and the routing graph from #44. |
| [#53](https://github.com/mskrado/diagnostic-agent/issues/53) Verification loop | [`exec/53-verification-loop.md`](exec/53-verification-loop.md) | **4th** — needs #52. |

Rules for the implementing agent of each issue:

1. **One issue = one branch = one PR.** Branch name and PR base are stated at the top of each spec file.
2. **Do only your issue's scope.** Each spec has a "What you are NOT doing" section — respect it.
3. **Keep names/paths/signatures exactly as written** unless a linter forces a trivial change.
4. **All four must land behind this overview PR (#54) merging first**, and stay `status:needs-spec` until
   a reviewer relabels them `status:ready`.
5. Run `pytest -q` before pushing; commit with `git commit -s` (DCO).

---

## 1. Purpose & scope

Give the diagnostic-agent the ability to **autonomously resolve a narrow, pre-approved class of
incidents** by running named, sandboxed runbook actions — while guaranteeing that a mistake can never
make an incident worse. This mirrors the article's system but is adapted to this codebase's
read-only, redaction-first, multi-tenant-image posture.

In scope:

- A **sandboxed runner** that executes only allowlisted commands in a locked-down container (#50).
- A **destructive-action classifier** that blocks dangerous actions before they reach the sandbox (#51).
- A structured, **parseable runbook step format** and an `execute_runbook` graph node (#52).
- A **post-execution verification loop** that confirms recovery before declaring resolution (#53).

## 2. Non-goals

- **No general-purpose remediation.** The agent runs *only* named runbook actions on the allowlist.
  There is no free-form shell, no LLM-authored commands, no "figure it out" execution.
- **No change to the default posture.** Execution is **opt-in per host and OFF by default**. A host
  that does not set `AGENT_EXEC_ENABLED=true` behaves exactly as today (read-only advisory).
- **No SEV1/SEV2 autonomy.** High-severity incidents always escalate to a human (enforced by #44 routing).
- **No new data-source writes.** Prometheus/Loki/Grafana clients remain read-only; execution acts on
  the host's own infrastructure only through the sandbox's declared, allowlisted surface.

---

## 3. Background & why this is a deliberate departure

Today the agent is read-only by design. `app/graph/schema.py` labels `fix_suggestions` as
"not auto-executed by the agent", `app/agent.py` enforces a fail-fast redaction guard, and the data
clients are HTTP GET only. The agent is **golden source** pinned as an image by host repos
(e.g. publishi.ai), so any execution capability is a *fleet-wide* liability, not a single-deployment
choice.

The article's author nearly restarted a primary production database at 2 a.m.; the only thing that
prevented it was the sandbox allowlist. We treat that near-miss as the primary design driver: the
sandbox and the destructive-action classifier are **load-bearing safety controls**, not conveniences.

---

## 4. Threat model & multi-tenant blast-radius analysis

**Assets at risk:** host production infrastructure, tenant data, the agent's credentials, and the
integrity of the incident signal (a bad "resolution" that masks an ongoing outage).

**Adversarial / failure vectors considered:**

| Vector | Example | Mitigation |
|---|---|---|
| Prompt/RAG steering | A runbook or log line coaxes the LLM into a destructive step | LLM never emits commands; only *selects* a named runbook action. Allowlist + classifier gate. |
| Runbook drift | An edited runbook adds `restart postgresql-primary` | Structured step must name an allowlisted action id; classifier flags destructive verbs; unknown ids fail closed. |
| Command injection | Parameters contain `; rm -rf /` | Actions are argv arrays, never shell strings; parameters are typed + validated against a schema; no shell interpolation. |
| Credential exfiltration | Action tries to read env/secrets or call out | Sandbox has no host secrets mounted, no network egress, no DB creds. |
| Blast radius across tenants | Action affects shared infra | Per-host opt-in; action scope bounded to the incident's declared service; no cross-service targeting. |
| Masking a real outage | Agent "resolves" without recovery | Verification loop (#53) requires the triggering signal to actually recover; otherwise escalate. |
| Retry storms | Same incident re-executed repeatedly | 24 h dedup guard ([#47](https://github.com/mskrado/diagnostic-agent/issues/47)) keyed on (service, alert_type). |

**Kill switch.** `AGENT_EXEC_ENABLED=false` (default) disables the entire branch at the graph edge.
A runtime kill switch (env re-read or `/admin/exec/disable`) is required so an operator can stop all
execution fleet-wide without a redeploy (see §9 open question O3).

---

## 5. Design principles (invariants)

1. **Default OFF, opt-in per host.** No behavior change unless `AGENT_EXEC_ENABLED=true`.
2. **Allowlist, not denylist.** Only explicitly enumerated actions can run; everything else fails closed.
3. **No shell, ever.** Actions are argv arrays run in a container; parameters are validated, never interpolated into a shell.
4. **Two independent gates before execution:** (a) the destructive-action classifier (#51), (b) the sandbox allowlist (#50). Either one refusing = no execution.
5. **Severity gate upstream.** SEV1/SEV2 never reach the execute branch (enforced by #44).
6. **Confirm, don't assume.** Resolution is only claimed after the verification loop (#53) observes recovery.
7. **Everything is redacted + audited.** All command output passes `redact_text()` before it touches audit, Slack, PagerDuty, or email. Every decision (attempted/blocked/resolved/escalated) is recorded.
8. **Fail toward escalation.** Any ambiguity, error, timeout, or classifier hit routes to a human with the full reasoning trace — never to silent success.

---

## 6. Architecture overview

Execution slots into the routed graph introduced by
[#44](https://github.com/mskrado/diagnostic-agent/issues/44). The linear pipeline
(`detect → retrieve → rag_lookup → correlate → report`) gains a conditional branch after `report`:

```
                         ┌─────────────► escalate ──► deliver (Slack/PD/email)
                         │  (SEV1/2, low confidence, no allowlisted match,
 report ──► should_route()   classifier hit, exec disabled, deduped)
                         │
                         └─► execute_runbook ──► verify ──┬─ recovered ─► resolve ──► deliver
                                                          └─ not recovered / error ─► escalate
```

Only `execute_runbook` and `verify` are new to Track B; `should_route` and `escalate` come from #44.
When `AGENT_EXEC_ENABLED=false`, `should_route` can never select the `execute_runbook` branch.

### State additions (`app/graph/state.py`)

```python
# --- Track B: execution ---
route: Literal["escalate", "execute", "report"]
matched_action: dict | None        # {runbook, action_id, params, target} chosen by selection
classifier_verdict: dict           # {destructive: bool, matched_patterns: [...], decision: "allow"|"hold"}
execution_result: dict | None      # {action_id, argv, exit_code, stdout, stderr, duration_s}
verification: dict | None          # {metric, before, after, recovered: bool, polled_s}
outcome: Literal["resolved", "escalated", "failed"]
```

---

## 7. Component spec — #50 Sandboxed runbook runner (Docker + allowlist)

**Module:** `app/execution/sandbox.py`

**Responsibility:** run a single allowlisted action as an argv array inside a disposable container and
return a structured result. It knows nothing about runbooks or graphs — it is a pure, testable executor.

### Action allowlist

Actions are declared in the integration profile (new `execution_profile.yaml`, resolved like other
profile files in `app/profile/`). Default preset ships **zero** actions; a host explicitly enables each.

```yaml
# execution_profile.yaml (host-supplied; empty by default)
version: 1
image: "ghcr.io/mskrado/diagnostic-agent-sandbox:1"   # minimal, pinned by digest in prod
actions:
  - id: clear-cdn-cache
    description: "Purge the CDN edge cache for the affected service"
    argv: ["cache-purge", "--service", "{service}", "--scope", "edge"]   # NO shell
    params:
      service: { type: "enum", from: "incident.service" }               # bound from state, validated
    scope: { services: ["web-gateway", "media-service"] }               # action may only target these
    timeout_s: 60
  - id: restart-worker-pool
    description: "Rolling restart of the stateless worker pool"
    argv: ["scale", "restart", "--pool", "{pool}", "--rolling"]
    params:
      pool: { type: "enum", values: ["ingest-workers", "render-workers"] }
    destructive: true            # forces classifier hold → human confirm (see #51)
    scope: { services: ["worker-pool"] }
    timeout_s: 180
```

### Container lockdown (required)

- **No network egress** (`--network none`) unless an action explicitly declares a bounded target;
  even then, no internet.
- **No host mounts** beyond a read-only action bundle; **no secrets**, **no DB creds**, no docker socket.
- Non-root user, read-only root FS, dropped capabilities (`--cap-drop ALL`), `--pids-limit`,
  memory/CPU limits, `--rm` (disposable).
- Hard `timeout_s` per action; the runner kills + reaps on timeout and reports `exit_code = -TIMEOUT`.

### Contract

```python
@dataclass
class ActionResult:
    action_id: str
    argv: list[str]
    exit_code: int          # 0 success; nonzero failure; negative = killed/timeout
    stdout: str             # redacted at the boundary before leaving the runner
    stderr: str             # redacted
    duration_s: float
    denied: bool            # True when the action id / params failed allowlist validation
    denial_reason: str | None

class Sandbox:
    def run(self, action_id: str, params: dict, *, service: str) -> ActionResult: ...
```

### Fail-closed rules

- Unknown `action_id` → `denied=True`, never executes.
- Param fails type/enum validation, or a param resolves outside `scope.services` → denied.
- `AGENT_EXEC_ENABLED=false` → `Sandbox.run` raises `ExecutionDisabled` (defense in depth; graph
  should not reach here).
- stdout/stderr are passed through `redact_text()` **inside** the runner before returning.

### Tests (#50 acceptance)

- Allowlisted action with valid params runs and returns `exit_code=0`.
- Unknown action id → `denied`, no container started.
- Param outside enum / outside `scope` → `denied`.
- Timeout kills the container and reports negative exit code.
- Attempted egress fails (network none).

---

## 8. Component spec — #51 Destructive-action classifier

**Module:** `app/execution/classifier.py`

**Responsibility:** the *first* of two gates. Before any action reaches the sandbox, decide whether it
is destructive and therefore requires human confirmation.

### Rules

- Matches configurable verb patterns against the action `id`, `description`, and `argv`:
  default patterns `restart|delete|drop|terminate|kill|truncate|purge|scale.*down|rm\b`.
- An action explicitly flagged `destructive: true` in `execution_profile.yaml` always holds.
- Verdict is one of:
  - `allow` — non-destructive; may proceed to the sandbox gate.
  - `hold` — destructive; **route to escalate with a "human confirmation required" note.** In this
    first iteration, `hold` never auto-runs even if confirmed asynchronously (confirm-then-run is a
    follow-up; see open question O2).

```python
@dataclass
class ClassifierVerdict:
    decision: Literal["allow", "hold"]
    destructive: bool
    matched_patterns: list[str]

def classify(action: AllowlistedAction, params: dict) -> ClassifierVerdict: ...
```

### Ordering

Classifier runs **before** the sandbox allowlist check inside `execute_runbook`. Both must pass for a
run to happen. The verdict is recorded in `state["classifier_verdict"]` and the reasoning trace.

### Tests (#51 acceptance)

- Positive: `restart-worker-pool` (verb + `destructive: true`) → `hold`.
- Negative: `clear-cdn-cache` → `allow`.
- Pattern config override adds/removes a verb and the verdict changes.
- A `hold` verdict routes to escalate and is present in the audit record.

---

## 9. Component spec — #52 `runbook_execute` node + structured runbook parsing

**Modules:** `app/execution/runbook.py` (parsing + selection), `app/graph/nodes.py` (new node),
`app/graph/build.py` (wiring).

### Structured runbook step format

Existing runbooks are prose used as RAG corpus and must **keep working unchanged**. Executable steps
are declared in an *optional* fenced block so a runbook without it stays advisory-only:

~~~markdown
## Automated actions
```runbook-actions
version: 1
match:
  alert_type: ["HighErrorRate", "GatewayUpstreamErrors"]
  service: ["web-gateway"]
  min_confidence: high        # gate on the LLM confidence_note
steps:
  - action_id: clear-cdn-cache
    when: "category == 'gateway'"    # optional guard evaluated against issue_categories
```
~~~

Parsing rules:

- The block is parsed to an `ExecutableRunbook`. A runbook with no `runbook-actions` block is
  **advisory-only** and can never be selected for execution.
- `action_id` **must** exist in `execution_profile.yaml`; otherwise the runbook is treated as
  advisory-only and a lint warning is emitted (`diag lint`).
- `match` constrains which incidents this runbook may act on; selection requires alert_type + service
  match **and** `confidence_note >= min_confidence`.

### Selection logic (inside `should_route` / `execute_runbook`)

1. Only reachable when `AGENT_EXEC_ENABLED=true`, severity ≥ SEV3, and not deduped (#47).
2. Find runbooks whose `match` covers the incident; require exactly one high-confidence match
   (ambiguity → escalate).
3. Bind `params` from state (`incident.service`, category, etc.), validated by the sandbox schema.
4. Run classifier (#51). `hold` → escalate.
5. Call `Sandbox.run` (#50). `denied` or nonzero exit → escalate with output.
6. On `exit_code == 0` → hand off to `verify` (#53).

### `execute_runbook` node contract

```python
def execute_runbook(state: DiagnosticState) -> DiagnosticState:
    # populates matched_action, classifier_verdict, execution_result
    # sets route/outcome; never raises — errors become an escalate outcome
```

### Tests (#52 acceptance)

- Prose-only runbook → parsed as advisory-only, never selected.
- Valid `runbook-actions` block with unknown `action_id` → advisory-only + lint warning.
- Ambiguous match (two runbooks) → escalate, no execution.
- Happy path: single high-confidence match → classifier allow → sandbox run → hands to verify.
- Classifier `hold` on a destructive step → escalate, no sandbox call.

---

## 10. Component spec — #53 Post-execution verification loop

**Module:** `app/execution/verify.py`, wired as the `verify` graph node.

**Responsibility:** confirm the incident's *triggering signal* actually recovered before claiming
resolution. This is what makes "zero bad resolutions" achievable.

### Behavior

- Determine the triggering metric/query from the alert (reuse the PromQL templates already in
  `metrics_profile.yaml` and the alert → metric mapping used by the `retrieve` node).
- Capture a `before` sample (from state, gathered pre-execution) and poll `after` samples on a fixed
  interval up to a bounded window:
  - `AGENT_EXEC_VERIFY_TIMEOUT_S` (default 180, matching the article's ~3 min)
  - `AGENT_EXEC_VERIFY_INTERVAL_S` (default 15)
- **Recovered** = the triggering condition is no longer met (e.g. `error_rate` back below threshold,
  `pending` back to 0) for two consecutive samples (debounce flapping).

### Outcomes

- Recovered → `outcome = "resolved"`: resolve the PagerDuty incident (#46), post the trace to Slack
  (#45), write audit. Record `verification.recovered = true`.
- Not recovered within timeout, or metric query error → `outcome = "escalated"`: page a human with the
  full trace **and** the fact that an automated action already ran (so the on-call knows the state).
- The action having run is *always* surfaced regardless of outcome.

### Tests (#53 acceptance)

- Metric recovers after N polls → `resolved`, PD resolve + Slack called.
- Metric never recovers within timeout → `escalated`, with "action already executed" in the trace.
- Metric query error mid-poll → `escalated` (fail toward human).
- Flapping (recovers then regresses within debounce) → not counted as recovered.

---

## 11. Configuration surface (`app/config.py`)

All default-OFF / conservative:

```python
exec_enabled: bool = False                 # AGENT_EXEC_ENABLED — master switch (default OFF)
exec_profile_path: str = ""                # resolves like other profile files; empty = no actions
exec_verify_timeout_s: int = 180           # AGENT_EXEC_VERIFY_TIMEOUT_S
exec_verify_interval_s: int = 15           # AGENT_EXEC_VERIFY_INTERVAL_S
exec_destructive_patterns: str = ""        # extra verbs, comma-separated; merged with defaults
exec_max_severity: str = "SEV3"            # never execute at or above SEV2 (defense in depth vs #44)
```

Startup guard (extends `_check_redaction` in `agent.py`): if `exec_enabled=true` but the resolved
`execution_profile.yaml` yields zero actions, **refuse to start** (a mis-mounted exec profile must not
silently disable a capability the operator believes is on) — mirroring the redaction fail-fast rule.

---

## 12. Audit, reasoning trace & delivery

- Every path writes an audit record (`app/delivery/audit.py`) including: `route`, `matched_action`,
  `classifier_verdict`, `execution_result` (redacted), `verification`, and final `outcome`.
- The reasoning trace posted to Slack (#45) / PagerDuty (#46) **always** states, in order: what was
  diagnosed, whether an action was selected, the classifier verdict, whether it executed, the
  command's redacted result, and whether the signal recovered.
- `redact_text()` is applied to stdout/stderr at the sandbox boundary **and** again at each delivery
  boundary (defense in depth).

---

## 13. Failure handling & rollback

- **No rollback of the action itself.** The allowlist is restricted to actions that are safe/idempotent
  or self-contained (e.g. cache purge, rolling restart of stateless pools). Actions that would need a
  rollback are `destructive: true` → classifier `hold` → human. This keeps the model simple and safe.
- Any exception in `execute_runbook`/`verify` is caught and converted to an `escalated` outcome; the
  graph never crashes a diagnosis (consistent with the existing broad-except pattern in `correlate`).
- Timeouts (sandbox and verify) both fail toward escalation.

---

## 14. Testing & rollout

**Gating:** #50–#53 remain `status:needs-spec` until this doc merges and the routing eval harness
(#48) is green. Each build issue lands behind its own feature branch + draft PR off `devel`.

**Test layers:**

1. Unit tests per component (contracts in §7–§10).
2. Sandbox integration test in CI using a throwaway image and a no-op allowlisted action.
3. Extend the incident-replay harness (#48) to assert the *execute* branch: for a fixture incident,
   assert selection → classifier verdict → (mock) sandbox → verify outcome, with **no real execution**.
4. A staged rollout: ship with `exec_enabled=false` everywhere; enable on a single non-critical host
   with a 1–2 action allowlist; measure auto-resolution + zero-bad-resolution before widening.

**Definition of done for Track B:** `exec_enabled=true` on a pilot host, ≥1 real incident auto-resolved
with verified recovery, zero incidents made worse, full trace visible in Slack + audit.

---

## 15. Open questions

- **O1.** Sandbox image ownership — do we publish `diagnostic-agent-sandbox` to GHCR alongside the
  agent, or let hosts supply their own image implementing the action CLI contract? (Leaning: publish a
  reference image; allow host override by digest.)
- **O2.** Confirm-then-run for `hold` actions — out of scope for the first iteration (destructive =
  always escalate). A later issue could add a Slack/PD approval callback that unblocks a held action.
- **O3.** Runtime kill switch mechanism — env re-read on each request vs. a small admin endpoint vs. a
  sentinel file. Needs a decision before pilot enablement.
- **O4.** Where the action CLI (`cache-purge`, `scale`, …) actually lives — it is host infrastructure
  tooling invoked inside the sandbox; the agent only knows argv + allowlist. Confirm the contract with
  the first pilot host.

---

## References

- Article: *I Replaced My Whole On-Call Rotation With a Multi-Agent System* (roiscale.ai).
- `docs/SDLC_GUIDE.md` — branching, DCO, release flow.
- Existing seams: `app/graph/build.py`, `app/graph/state.py`, `app/graph/schema.py`,
  `app/delivery/redact.py`, `app/config.py`, `runbooks/`.
