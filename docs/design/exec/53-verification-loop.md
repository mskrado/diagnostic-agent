# Implementation Spec — #53 Post-execution verification loop

> **Read this whole file before writing code.** Follow it literally.

| | |
|---|---|
| **Issue** | [#53](https://github.com/mskrado/diagnostic-agent/issues/53) |
| **Depends on** | #52 (the `execute_runbook` node + graph branch). Uses the existing `PrometheusClient`. Delivery on resolve/escalate ideally uses #45 (Slack) / #46 (PagerDuty) if merged; if not, degrade to the existing email/annotation + audit. |
| **Blocks** | Nothing (last Track B build issue). |
| **Branch to create** | `feature/verification-loop-53` off `devel` |
| **Draft PR base** | `devel` · title `[core] Post-execution verification loop (#53)` · body `Closes #53` |

---

## 1. Goal

After an action runs, poll the incident's **triggering metric** for a bounded window and only declare
`resolved` if it recovers. Otherwise `escalate` — including the fact that an action already ran.

## 2. What you are NOT doing

- NOT re-running the action.
- NOT inventing new PromQL. Reuse the query the `retrieve` node already built for this alert.
- NOT resolving PagerDuty here if #46 isn't merged — record the outcome in the audit record and let the
  existing delivery run. Add the PD-resolve call behind a `hasattr`/flag check.

---

## 3. Files to create / modify

**Create:**
1. `app/execution/verify.py`
2. `tests/test_verify.py`

**Modify:**
3. `app/config.py` — add the two verify fields (§4).
4. `app/graph/nodes.py` — add the `verify` node method (§6).
5. `app/graph/build.py` — add the `verify` node + edges (§7).

---

## 4. Config fields (`app/config.py`, in the `# --- Execution ---` block)

```python
    # Verification loop: how long to wait for the triggering signal to recover.
    exec_verify_timeout_s: int = 180
    exec_verify_interval_s: int = 15
```

---

## 5. What "recovered" means

The triggering metric must **no longer breach** for **two consecutive** polls (debounce flapping).
For the common alerts:

| Alert family | Recovered when |
|---|---|
| HighErrorRate | `error_rate` back below the alert threshold (or ~0 when threshold unknown) |
| db pool pending | `db_pool_pending` == 0 |
| service down | `service_up` == 1 |

Because thresholds live in Alertmanager, this issue uses a **simple, safe rule**: recovered = the
triggering metric value is `0` (or below a configured floor) for two consecutive samples. If the metric
can't be re-queried, treat as **not recovered** (fail toward human).

---

## 6. Reference implementation

### `app/execution/verify.py`

```python
"""Post-execution verification: poll the triggering metric until recovery or timeout."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    metric_query: str
    recovered: bool
    samples: list[float]
    polled_s: float


def verify_recovery(
    prom,
    metric_query: str,
    *,
    timeout_s: int,
    interval_s: int,
    floor: float = 0.0,
    sleep=time.sleep,
    now=time.monotonic,
) -> VerificationResult:
    """Poll `metric_query` until it is <= floor for TWO consecutive samples, or timeout.

    `prom` is a PrometheusClient (has .instant(query) -> float | None).
    `sleep`/`now` are injectable for tests.
    """
    start = now()
    samples: list[float] = []
    consecutive_ok = 0
    while (now() - start) < timeout_s:
        value = prom.instant(metric_query)
        if value is None:
            # Cannot confirm -> fail toward human.
            return VerificationResult(metric_query, False, samples, round(now() - start, 2))
        samples.append(value)
        if value <= floor:
            consecutive_ok += 1
            if consecutive_ok >= 2:
                return VerificationResult(metric_query, True, samples, round(now() - start, 2))
        else:
            consecutive_ok = 0
        sleep(interval_s)
    return VerificationResult(metric_query, False, samples, round(now() - start, 2))
```

### `verify` node (`app/graph/nodes.py`)

```python
    # ---- verify (Track B) ---------------------------------------------
    def verify(self, state: DiagnosticState) -> DiagnosticState:
        """Confirm the triggering signal recovered; set final outcome."""
        from ..execution.verify import verify_recovery

        # Reuse the query the retrieve node built. Store it under log_source/prom
        # or recompute here from the alert; simplest: use the error_rate query for
        # the service from the metrics profile.
        query = self._triggering_query(state)
        if not query:
            return {**state, "outcome": "escalated", "route": "escalate"}
        try:
            result = verify_recovery(
                self.prom, query,
                timeout_s=settings.exec_verify_timeout_s,
                interval_s=settings.exec_verify_interval_s,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("verify failed: %s", exc)
            return {**state, "outcome": "escalated", "route": "escalate"}

        state_out = {**state, "verification": result.__dict__}
        if result.recovered:
            return {**state_out, "outcome": "resolved", "route": "report"}
        return {**state_out, "outcome": "escalated", "route": "escalate"}

    def _triggering_query(self, state: DiagnosticState) -> str:
        """Best-effort PromQL for the metric that triggered the alert."""
        from ..profile import get_profile
        service = state.get("service", "") or ""
        metrics = get_profile().metrics
        try:
            return metrics.render("error_rate", service=service, window=settings.metrics_window) or ""
        except Exception:  # noqa: BLE001
            return ""
```

> Add `verification: dict` to `DiagnosticState` if #52 did not already.

---

## 7. Graph wiring (`app/graph/build.py`)

```python
    graph.add_node("verify", nodes.verify)
    graph.add_edge("execute_runbook", "verify")   # replaces the temporary #52 TODO edge
    graph.add_conditional_edges(
        "verify",
        lambda s: s.get("route", "escalate"),
        {
            "report": END,          # or the #44 deliver node (resolved path)
            "escalate": "escalate", # node from #44
        },
    )
```

---

## 8. Tests (`tests/test_verify.py`)

`verify_recovery` is fully unit-testable with a fake prom + injected `sleep`/`now`.

```python
from app.execution.verify import verify_recovery


class _FakeProm:
    def __init__(self, values):
        self._values = list(values)
        self.queries = []

    def instant(self, query):
        self.queries.append(query)
        return self._values.pop(0) if self._values else 0.0


class _Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_recovers_after_two_zero_samples():
    prom = _FakeProm([5.0, 0.0, 0.0])   # breach, then two zeros
    clock = _Clock()
    res = verify_recovery(prom, "q", timeout_s=100, interval_s=10,
                          sleep=clock.sleep, now=clock.now)
    assert res.recovered is True


def test_never_recovers_times_out():
    prom = _FakeProm([5.0] * 100)
    clock = _Clock()
    res = verify_recovery(prom, "q", timeout_s=30, interval_s=10,
                          sleep=clock.sleep, now=clock.now)
    assert res.recovered is False


def test_metric_query_error_is_not_recovered():
    class _NoneProm:
        def instant(self, q):
            return None
    res = verify_recovery(_NoneProm(), "q", timeout_s=30, interval_s=10)
    assert res.recovered is False


def test_flapping_does_not_count_as_recovered():
    # zero, breach, zero -> never two consecutive zeros before values run out
    prom = _FakeProm([0.0, 5.0, 0.0])
    clock = _Clock()
    res = verify_recovery(prom, "q", timeout_s=25, interval_s=10,
                          sleep=clock.sleep, now=clock.now)
    assert res.recovered is False
```

---

## 9. Definition of done

- [ ] Metric returns to `<= floor` for two consecutive polls → `recovered=True`, node sets `outcome="resolved"`.
- [ ] Metric never recovers within `exec_verify_timeout_s` → `recovered=False`, `outcome="escalated"`.
- [ ] `prom.instant` returns `None` mid-poll → not recovered (fail toward human).
- [ ] Flapping (single zero then breach) → not recovered.
- [ ] The fact that an action ran is preserved in state/audit on both outcomes.
- [ ] `pytest -q` green; DCO-signed commit; draft PR base `devel`.
