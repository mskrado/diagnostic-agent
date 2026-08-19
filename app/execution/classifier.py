"""Destructive-action classifier for allowlisted runbook actions.

Pure decision function: given an allowlisted action and optional resolved params,
decide whether it is destructive and must be held for a human. Destructive =
matches a verb pattern OR is explicitly flagged destructive in the profile.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from .. import config as config_mod
from ..profile.models import AllowlistedAction

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
    extra = (config_mod.settings.exec_destructive_patterns or "").strip()
    if not extra:
        return patterns
    for frag in extra.split(","):
        frag = frag.strip()
        if not frag:
            continue
        if any(c in frag for c in r"\.[]()*+?"):
            patterns.append(frag)
        else:
            patterns.append(rf"\b{re.escape(frag)}\b")
    return patterns


def classify(
    action: AllowlistedAction, params: dict | None = None
) -> ClassifierVerdict:
    """Return a verdict. `hold` means: do not run; escalate to a human."""
    del params  # Reserved for future richer classification inputs.

    if action.destructive:
        return ClassifierVerdict(
            decision="hold",
            destructive=True,
            matched_patterns=["profile:destructive=true"],
        )

    haystack = " ".join([action.id, action.description, *action.argv]).lower()
    matched: list[str] = []
    for pattern in _active_patterns():
        if re.search(pattern, haystack, re.IGNORECASE):
            matched.append(pattern)

    if matched:
        return ClassifierVerdict(
            decision="hold",
            destructive=True,
            matched_patterns=matched,
        )
    return ClassifierVerdict(
        decision="allow",
        destructive=False,
        matched_patterns=[],
    )
