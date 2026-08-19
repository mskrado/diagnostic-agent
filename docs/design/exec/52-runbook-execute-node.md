# Implementation Spec — #52 `runbook_execute` node + structured runbook parsing

> **Read this whole file before writing code.** Follow it literally.

| | |
|---|---|
| **Issue** | [#52](https://github.com/mskrado/diagnostic-agent/issues/52) |
| **Depends on** | #50 (Sandbox), #51 (classifier), and #44 (conditional routing graph). If #44 is not merged yet, coordinate — this issue adds a node to the graph #44 introduces. |
| **Blocks** | #53 |
| **Branch to create** | `feature/runbook-execute-node-52` off `devel` |
| **Draft PR base** | `devel` · title `[core] runbook_execute node + structured parsing (#52)` · body `Closes #52` |

---

## 1. Goal

1. Parse an **optional** ` ```runbook-actions ` block out of a runbook `.md` file into a structured object.
2. Add a graph node `execute_runbook` that: picks a matching runbook, binds params, runs the classifier
   (#51), runs the sandbox (#50), and records the outcome in state. It **never raises** — any problem
   becomes an `escalate` route.

## 2. What you are NOT doing

- NOT implementing the verification loop (#53). This node hands off to `verify` when the action ran with exit code 0.
- NOT changing how prose runbooks feed RAG. A runbook with **no** `runbook-actions` block stays advisory-only and must be ignored by this node.
- NOT running destructive actions. If the classifier says `hold`, route to escalate.

---

## 3. Files to create / modify

**Create:**
1. `app/execution/runbook.py`  — parser + selection.
2. `tests/test_runbook_parse.py`
3. `tests/test_execute_node.py`

**Modify:**
4. `app/graph/state.py`  — add the execution fields (§4).
5. `app/graph/nodes.py`  — add the `execute_runbook` method (§6).
6. `app/graph/build.py`  — wire the node onto the automation-candidate branch (§7).

---

## 4. State fields (add to `app/graph/state.py`, inside `DiagnosticState`)

```python
    # --- execution (Track B) ---
    route: str                 # "escalate" | "execute" | "report" (set by routing, #44)
    matched_action: dict       # {"runbook": str, "action_id": str, "params": dict}
    classifier_verdict: dict   # ClassifierVerdict as a dict
    execution_result: dict     # ActionResult as a dict
    outcome: str               # "resolved" | "escalated" | "failed"
```

Add `from typing import ... ` only if needed; these are plain `dict`/`str` so no new imports required.

---

## 5. Runbook action block format + parser (`app/execution/runbook.py`)

### The block authors add to a runbook `.md`

~~~markdown
## Automated actions
```runbook-actions
version: 1
match:
  alert_type: ["HighErrorRate", "GatewayUpstreamErrors"]
  service: ["web-gateway"]
  min_confidence: high
steps:
  - action_id: clear-cdn-cache
```
~~~

### Reference implementation

```python
"""Parse and select executable runbook actions.

A runbook .md MAY contain one fenced ```runbook-actions block (YAML). A runbook
without it is advisory-only and is never selected for execution.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..profile import get_profile

_BLOCK_RE = re.compile(r"```runbook-actions\s*\n(.*?)\n```", re.DOTALL)

_CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


@dataclass
class RunbookStep:
    action_id: str


@dataclass
class ExecutableRunbook:
    path: str
    alert_types: list[str] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    min_confidence: str = "high"
    steps: list[RunbookStep] = field(default_factory=list)


def parse_runbook_actions(text: str, *, path: str = "") -> ExecutableRunbook | None:
    """Return an ExecutableRunbook if the text has a runbook-actions block, else None."""
    m = _BLOCK_RE.search(text)
    if not m:
        return None
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    match = data.get("match") or {}
    steps = [
        RunbookStep(action_id=str(s["action_id"]))
        for s in (data.get("steps") or [])
        if isinstance(s, dict) and s.get("action_id")
    ]
    return ExecutableRunbook(
        path=path,
        alert_types=[str(a) for a in (match.get("alert_type") or [])],
        services=[str(s) for s in (match.get("service") or [])],
        min_confidence=str(match.get("min_confidence", "high")),
        steps=steps,
    )


def load_executable_runbooks(runbooks_dir: str | None) -> list[ExecutableRunbook]:
    """Parse every .md in runbooks_dir; keep only those with a runbook-actions block
    whose steps all reference a known allowlisted action id."""
    if not runbooks_dir:
        return []
    exec_profile = get_profile().execution
    out: list[ExecutableRunbook] = []
    for md in Path(runbooks_dir).glob("*.md"):
        try:
            rb = parse_runbook_actions(md.read_text(encoding="utf-8"), path=str(md))
        except OSError:
            continue
        if rb is None or not rb.steps:
            continue
        # Every step must map to a known action; otherwise treat as advisory-only.
        if all(exec_profile.get(s.action_id) is not None for s in rb.steps):
            out.append(rb)
    return out


def select_runbook(
    runbooks: list[ExecutableRunbook],
    *,
    alert_type: str,
    service: str,
    confidence_note: str,
) -> ExecutableRunbook | None:
    """Return the single matching runbook, or None when zero or MORE THAN ONE match
    (ambiguity must escalate, never guess)."""
    conf = _CONFIDENCE_ORDER.get((confidence_note or "").lower(), -1)
    matches = [
        rb for rb in runbooks
        if (not rb.alert_types or alert_type in rb.alert_types)
        and (not rb.services or service in rb.services)
        and conf >= _CONFIDENCE_ORDER.get(rb.min_confidence, 2)
    ]
    return matches[0] if len(matches) == 1 else None
```

---

## 6. The node (`app/graph/nodes.py`)

Add these imports near the top of the file:

```python
from ..execution.classifier import classify
from ..execution.runbook import load_executable_runbooks, select_runbook
from ..execution.sandbox import Sandbox
```

Add a `Sandbox` to the constructor so it is injected once (edit `__init__`):

```python
    def __init__(self, prom, loki, grafana, dep_map, rag, llm, sandbox=None):
        ...
        self.sandbox = sandbox   # Sandbox() in production; may be a fake in tests
```

> Update the construction site in `app/agent.py` to pass `Sandbox()` (import from `app.execution.sandbox`).
> Existing test constructions that pass 6 positional args keep working because `sandbox` defaults to `None`.

Add the node method (place it after `report`):

```python
    # ---- execute_runbook (Track B) ------------------------------------
    def execute_runbook(self, state: DiagnosticState) -> DiagnosticState:
        """Run a matched, non-destructive, allowlisted action in the sandbox.

        Never raises. Any problem -> outcome 'escalated' (a human takes over).
        Only reachable on the automation-candidate branch (see build.py); the
        severity gate + exec_enabled check happen upstream in routing (#44).
        """
        service = state.get("service", "") or ""
        alert_type = state.get("alert_type", "") or ""
        diagnosis = state.get("hypotheses", {}) or {}
        confidence = str(diagnosis.get("confidence_note", "low"))

        try:
            runbooks = load_executable_runbooks(settings.resolved_runbooks_path())
            rb = select_runbook(
                runbooks, alert_type=alert_type, service=service, confidence_note=confidence
            )
            if rb is None:
                return {**state, "route": "escalate", "outcome": "escalated"}

            step = rb.steps[0]
            action = get_profile().execution.get(step.action_id)
            if action is None:
                return {**state, "route": "escalate", "outcome": "escalated"}

            verdict = classify(action)
            matched_action = {
                "runbook": rb.path, "action_id": action.id, "params": {},
            }
            if verdict.decision == "hold":
                return {
                    **state,
                    "matched_action": matched_action,
                    "classifier_verdict": verdict.__dict__,
                    "route": "escalate",
                    "outcome": "escalated",
                }

            result = self.sandbox.run(action.id, {}, service=service)
            state_out = {
                **state,
                "matched_action": matched_action,
                "classifier_verdict": verdict.__dict__,
                "execution_result": result.__dict__,
            }
            if result.denied or result.exit_code != 0:
                return {**state_out, "route": "escalate", "outcome": "escalated"}
            # Success so far -> hand off to verify (#53).
            return {**state_out, "route": "execute"}
        except Exception as exc:  # noqa: BLE001 - never crash the graph
            logger.error("execute_runbook failed: %s", exc)
            return {**state, "route": "escalate", "outcome": "escalated"}
```

---

## 7. Graph wiring (`app/graph/build.py`)

This issue assumes #44 added a routing function after `report`. Add the execution node and edges. The
final shape:

```python
    graph.add_node("execute_runbook", nodes.execute_runbook)
    # #44 provides should_route; on "execute" it must send to execute_runbook.
    # On "escalate"/"report" it goes to the #44 escalate/deliver nodes.
    graph.add_conditional_edges(
        "report",
        should_route,                       # from #44
        {
            "escalate": "escalate",         # node from #44
            "execute": "execute_runbook",
            "report": END,                  # or the #44 deliver node
        },
    )
    # After execution, #53 adds a `verify` node; until then, route to escalate/END.
    graph.add_edge("execute_runbook", "verify")   # `verify` node added by #53
```

> If #53 is not merged yet, temporarily `graph.add_edge("execute_runbook", END)` and leave a `# TODO(#53)`.
> The `verify` edge is the correct final wiring.

---

## 8. Tests

### `tests/test_runbook_parse.py`

```python
from app.execution.runbook import parse_runbook_actions, select_runbook, ExecutableRunbook, RunbookStep


PROSE_ONLY = "# Runbook\n\n## Symptoms\n- things break\n"

WITH_BLOCK = (
    "# Runbook\n\n## Automated actions\n"
    "```runbook-actions\n"
    "version: 1\n"
    "match:\n"
    "  alert_type: [HighErrorRate]\n"
    "  service: [web-gateway]\n"
    "  min_confidence: high\n"
    "steps:\n"
    "  - action_id: clear-cdn-cache\n"
    "```\n"
)


def test_prose_only_runbook_returns_none():
    assert parse_runbook_actions(PROSE_ONLY) is None


def test_block_parsed():
    rb = parse_runbook_actions(WITH_BLOCK, path="rb.md")
    assert rb is not None
    assert rb.alert_types == ["HighErrorRate"]
    assert rb.services == ["web-gateway"]
    assert rb.min_confidence == "high"
    assert rb.steps[0].action_id == "clear-cdn-cache"


def _rb(**kw):
    base = dict(path="rb.md", alert_types=["HighErrorRate"], services=["web-gateway"],
                min_confidence="high", steps=[RunbookStep("clear-cdn-cache")])
    base.update(kw)
    return ExecutableRunbook(**base)


def test_select_single_match():
    rb = select_runbook([_rb()], alert_type="HighErrorRate", service="web-gateway",
                        confidence_note="high")
    assert rb is not None


def test_select_low_confidence_no_match():
    rb = select_runbook([_rb()], alert_type="HighErrorRate", service="web-gateway",
                        confidence_note="low")
    assert rb is None


def test_select_ambiguous_returns_none():
    rb = select_runbook([_rb(), _rb(path="other.md")], alert_type="HighErrorRate",
                        service="web-gateway", confidence_note="high")
    assert rb is None
```

### `tests/test_execute_node.py`

Use a fake sandbox object so no Docker is required.

```python
import types

from app.graph.nodes import DiagnosticNodes
from app.execution.sandbox import ActionResult


class _FakeSandbox:
    def __init__(self, result):
        self._result = result
        self.calls = []

    def run(self, action_id, params, *, service):
        self.calls.append((action_id, service))
        return self._result


def _nodes(sandbox):
    # Only execute_runbook is exercised; other collaborators are unused here.
    return DiagnosticNodes(None, None, None, None, None, None, sandbox=sandbox)


def _state(confidence="high"):
    return {
        "service": "web-gateway",
        "alert_type": "HighErrorRate",
        "hypotheses": {"confidence_note": confidence},
    }
```

> The node reads real runbooks + the execution profile via `get_profile()`. For a deterministic test,
> either (a) point `AGENT_RUNBOOKS_PATH` + `AGENT_PROFILE_DIR` at a tmp fixture containing one runbook
> with a `runbook-actions` block and an `execution_profile.yaml`, then `reset_profile_cache()`, or
> (b) monkeypatch `app.graph.nodes.load_executable_runbooks` and `app.graph.nodes.get_profile`.
> Write at least these cases:
> - success path: fake sandbox returns `ActionResult(exit_code=0, denied=False, ...)` → `route == "execute"`.
> - denied/nonzero exit → `route == "escalate"`, `outcome == "escalated"`.
> - destructive action (`classify` returns hold) → `route == "escalate"`, sandbox NOT called.
> - no matching runbook → `route == "escalate"`.

---

## 9. Definition of done

- [ ] Prose-only runbook → `parse_runbook_actions` returns `None`; never selected.
- [ ] `runbook-actions` block with an unknown `action_id` → excluded by `load_executable_runbooks`.
- [ ] Ambiguous match (2 runbooks) → `select_runbook` returns `None` → escalate.
- [ ] Success path sets `route == "execute"` and populates `execution_result`.
- [ ] Classifier `hold` → escalate, sandbox not called.
- [ ] Node never raises (wrap in try/except → escalate).
- [ ] `pytest -q` green; DCO-signed commit; draft PR base `devel`.
