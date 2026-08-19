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
    match = _BLOCK_RE.search(text)
    if not match:
        return None
    try:
        data = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    match_block = data.get("match") or {}
    steps = [
        RunbookStep(action_id=str(step["action_id"]))
        for step in (data.get("steps") or [])
        if isinstance(step, dict) and step.get("action_id")
    ]
    return ExecutableRunbook(
        path=path,
        alert_types=[str(alert_type) for alert_type in (match_block.get("alert_type") or [])],
        services=[str(service) for service in (match_block.get("service") or [])],
        min_confidence=str(match_block.get("min_confidence", "high")),
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
            runbook = parse_runbook_actions(md.read_text(encoding="utf-8"), path=str(md))
        except OSError:
            continue
        if runbook is None or not runbook.steps:
            continue
        if all(exec_profile.get(step.action_id) is not None for step in runbook.steps):
            out.append(runbook)
    return out


def select_runbook(
    runbooks: list[ExecutableRunbook],
    *,
    alert_type: str,
    service: str,
    confidence_note: str,
) -> ExecutableRunbook | None:
    """Return the single matching runbook, or None when zero or MORE THAN ONE match."""
    confidence = _CONFIDENCE_ORDER.get((confidence_note or "").lower(), -1)
    matches = [
        runbook
        for runbook in runbooks
        if (not runbook.alert_types or alert_type in runbook.alert_types)
        and (not runbook.services or service in runbook.services)
        and confidence >= _CONFIDENCE_ORDER.get(runbook.min_confidence, 2)
    ]
    return matches[0] if len(matches) == 1 else None
