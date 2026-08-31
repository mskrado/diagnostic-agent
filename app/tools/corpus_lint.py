"""Offline corpus lint — no LLM credentials required.

Checks that every runbook in a workspace has a scenario (and vice versa), that
blind-eval cases' must_reference tokens appear in their injected logs, and that
runbooks keep the hypotheses-only framing the agent's prompts rely on.

Checks whose inputs a workspace does not provide are skipped and reported as
notes, so a host can adopt the corpus incrementally.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..workspace import Workspace
from . import scenarios as scenarios_tool


@dataclass
class LintResult:
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _load_yaml(path: Path):
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def check_runbook_scenarios(workspace: Workspace, result: LintResult) -> None:
    if workspace.scenarios_path is None:
        result.notes.append("no scenarios file — skipped runbook/scenario coverage")
        return
    missing, extra = scenarios_tool.coverage_gaps(workspace)
    if missing:
        result.errors.append(f"runbooks without scenarios: {sorted(missing)}")
    if extra:
        result.errors.append(f"scenarios without runbooks: {sorted(extra)}")


def check_blind_eval_grounding(workspace: Workspace, result: LintResult) -> None:
    if workspace.blind_eval_path is None:
        result.notes.append("no blind-eval dataset — skipped grounding check")
        return
    data = _load_yaml(workspace.blind_eval_path)
    for case in data.get("cases") or []:
        cid = case.get("id", "?")
        logs = "\n".join(case.get("logs") or []).lower()
        expected = case.get("expected") or {}
        for token in expected.get("must_reference") or []:
            if str(token).lower() not in logs:
                result.errors.append(
                    f"blind eval {cid}: must_reference token {token!r} not found in logs"
                )


_AUTO_REMEDIATE = re.compile(
    r"\b(auto-?remediat|automatically (fix|restart|scale|delete|kill))\b",
    re.IGNORECASE,
)

# Must match app.draft.runbook_llm.DRAFT_MARKER's distinctive prefix so a human
# edit that leaves other HTML comments alone still clears the error.
_DRAFT_MARKER_RE = re.compile(
    r"<!--\s*DRAFT:\s*edit before relying on this runbook",
    re.IGNORECASE,
)


def check_hypotheses_only(workspace: Workspace, result: LintResult) -> None:
    if workspace.runbooks_dir is None:
        result.notes.append("no runbooks directory — skipped hypotheses-only check")
        return
    for path in sorted(workspace.runbooks_dir.glob("runbook-*.md")):
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(workspace.root)
        if "Hypotheses-only" not in text and "hypotheses only" not in text.lower():
            result.errors.append(f"{rel}: missing Hypotheses-only section")
        if _AUTO_REMEDIATE.search(text) and "Do NOT auto-remediate" not in text:
            result.errors.append(
                f"{rel}: possible auto-remediation wording without disclaimer"
            )


def check_draft_runbooks(workspace: Workspace, result: LintResult) -> None:
    """Reject skeletons left over from ``diag draft --llm`` until edited.

    Draft runbooks are generated as a unit with their scenarios so the
    bijection holds, but they must not silently count as production corpus.
    Removing the DRAFT marker (after a human edits the prose) clears this error.
    """
    if workspace.runbooks_dir is None:
        return
    drafts: list[str] = []
    for path in sorted(workspace.runbooks_dir.glob("runbook-*.md")):
        text = path.read_text(encoding="utf-8")
        if _DRAFT_MARKER_RE.search(text):
            drafts.append(str(path.relative_to(workspace.root)))
    if drafts:
        result.errors.append(
            "draft runbooks still marked DRAFT (edit and remove the marker): "
            + ", ".join(drafts)
        )


def lint(workspace: Workspace) -> LintResult:
    """Run every corpus check the workspace has inputs for."""
    result = LintResult()
    check_runbook_scenarios(workspace, result)
    check_blind_eval_grounding(workspace, result)
    check_hypotheses_only(workspace, result)
    check_draft_runbooks(workspace, result)
    return result
