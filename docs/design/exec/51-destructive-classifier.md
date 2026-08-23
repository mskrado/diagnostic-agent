# Implementation Spec — #51 Destructive-action classifier

> **Read this whole file before writing code.** Follow it literally.

| | |
|---|---|
| **Status** | **Implemented** — `app/execution/classifier.py`, `tests/test_classifier.py`. Deviations recorded in [`../sandboxed-execution.md`](../sandboxed-execution.md#as-built-deviations-from-this-spec) |
| **Issue** | [#51](https://github.com/mskrado/diagnostic-agent/issues/51) |
| **Depends on** | #50 merged (needs `AllowlistedAction` model + `app/execution/` package). |
| **Blocks** | #52 |
| **Branch to create** | `feature/destructive-classifier-51` off `devel` |
| **Draft PR base** | `devel` · title `[core] Destructive-action classifier (#51)` · body `Closes #51` |

---

## 1. Goal (one sentence)

Add a function that looks at an allowlisted action and decides whether it is **destructive** (e.g.
restart/delete/drop/terminate/kill). Destructive actions must be **held** — routed to a human — and
never auto-run.

## 2. What you are NOT doing

- NOT running anything. This is a pure, side-effect-free decision function.
- NOT wiring it into the graph. That is #52 (it calls this function).
- NOT changing the sandbox.

---

## 3. Files to create / modify

**Create:**
1. `app/execution/classifier.py`
2. `tests/test_classifier.py`

**Modify:**
3. `app/config.py` — add one field (§4).

---

## 4. Config field (add to `app/config.py`, in the `# --- Execution ---` block added by #50)

```python
    # Extra destructive verb patterns (comma-separated regex fragments), merged
    # with the built-in defaults. Example: "flush,evict".
    exec_destructive_patterns: str = ""
```

---

## 5. Reference implementation (`app/execution/classifier.py`)

```python
"""Destructive-action classifier — the first of two gates before execution.

Pure decision function: given an allowlisted action and its resolved params,
decide whether it is destructive and must be held for a human. Destructive =
matches a verb pattern OR is explicitly flagged destructive in the profile.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from ..config import settings
from ..profile.models import AllowlistedAction

# Built-in destructive verbs. Word-boundary matched, case-insensitive.
_DEFAULT_PATTERNS: tuple[str, ...] = (
    r"\brestart\b",
    r"\bdelete\b",
    r"\bdrop\b",
    r"\bterminate\b",
    r"\bkill\b",
    r"\btruncate\b",
    r"\bpurge\b",
    r"\bwipe\b",
    r"\bscale\b.*\bdown\b",
    r"\brm\b",
)


@dataclass
class ClassifierVerdict:
    decision: Literal["allow", "hold"]
    destructive: bool
    matched_patterns: list[str] = field(default_factory=list)


def _active_patterns() -> list[str]:
    patterns = list(_DEFAULT_PATTERNS)
    extra = (settings.exec_destructive_patterns or "").strip()
    if extra:
        for frag in extra.split(","):
            frag = frag.strip()
            if frag:
                # Wrap bare words in word boundaries; leave regex-looking fragments as-is.
                patterns.append(frag if any(c in frag for c in r"\.[]()*+?") else rf"\b{re.escape(frag)}\b")
    return patterns


def classify(action: AllowlistedAction, params: dict | None = None) -> ClassifierVerdict:
    """Return a verdict. `hold` means: do NOT run; escalate to a human."""
    # 1. Explicit profile flag always wins.
    if action.destructive:
        return ClassifierVerdict(decision="hold", destructive=True,
                                 matched_patterns=["profile:destructive=true"])

    # 2. Pattern match against id + description + argv tokens.
    haystack = " ".join([action.id, action.description, *action.argv]).lower()
    matched: list[str] = []
    for pat in _active_patterns():
        if re.search(pat, haystack, re.IGNORECASE):
            matched.append(pat)

    if matched:
        return ClassifierVerdict(decision="hold", destructive=True, matched_patterns=matched)
    return ClassifierVerdict(decision="allow", destructive=False, matched_patterns=[])
```

---

## 6. Step-by-step checklist

1. Create branch `feature/destructive-classifier-51` off `devel`.
2. Add the config field (§4).
3. Create `app/execution/classifier.py` (§5).
4. Write tests (§7). Run `pytest -q`.
5. Commit `-s`, push, open draft PR (base `devel`, `Closes #51`).

---

## 7. Tests (`tests/test_classifier.py`)

```python
from app import config as config_mod
from app.execution.classifier import classify
from app.profile.models import AllowlistedAction


def _action(**kw) -> AllowlistedAction:
    base = dict(
        id="clear-cdn-cache",
        description="Purge the CDN edge cache",
        argv=("cache-purge", "--service", "{service}"),
        params=(),
        scope_services=("web-gateway",),
        destructive=False,
        timeout_s=60,
    )
    base.update(kw)
    return AllowlistedAction(**base)


def test_profile_destructive_flag_forces_hold():
    v = classify(_action(destructive=True))
    assert v.decision == "hold" and v.destructive is True


def test_restart_verb_in_argv_holds():
    v = classify(_action(id="restart-worker-pool",
                         description="Rolling restart",
                         argv=("scale", "restart", "--pool", "{pool}")))
    assert v.decision == "hold"
    assert any("restart" in p for p in v.matched_patterns)


def test_non_destructive_action_allows():
    # "purge" IS destructive by default; use a benign id/description/argv here.
    v = classify(_action(id="warm-cache", description="Warm the cache",
                         argv=("cache-warm", "--service", "{service}")))
    assert v.decision == "allow" and v.destructive is False


def test_extra_pattern_from_config_holds(monkeypatch):
    monkeypatch.setenv("AGENT_EXEC_DESTRUCTIVE_PATTERNS", "evict")
    config_mod.settings = config_mod.Settings()
    v = classify(_action(id="evict-tenant", description="Evict tenant sessions",
                         argv=("evict", "--tenant", "{tenant}")))
    assert v.decision == "hold"
```

> Note: the default patterns treat `purge` as destructive, so do not use a "purge" example as the benign
> case. The reference test above uses `warm-cache` for the allow case on purpose.

---

## 8. Definition of done

- [ ] `destructive: true` in the profile → always `hold`.
- [ ] An action whose id/description/argv contains a default verb (restart/delete/drop/…) → `hold` with the matched pattern recorded.
- [ ] A benign action → `allow`, `destructive=False`.
- [ ] `AGENT_EXEC_DESTRUCTIVE_PATTERNS` adds a verb that then triggers `hold`.
- [ ] `pytest -q` green; DCO-signed commit; draft PR base `devel`.
