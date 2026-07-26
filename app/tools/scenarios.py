"""Load and validate a workspace's runbook scenarios.

A scenario ties an alert (labels + annotations) to the runbook that should be
retrieved for it, which drives both offline coverage checks and live E2E runs
against a running agent.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from ..workspace import Workspace, WorkspaceError


def load_scenarios(workspace: Workspace) -> list[dict]:
    """Return the workspace's scenarios, or raise if it declares none."""
    path = workspace.scenarios_path
    if path is None:
        raise WorkspaceError(
            f"no scenarios file in {workspace.root} "
            "(expected scenarios.yaml). See `diag init`."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    scenarios = raw.get("scenarios") or []
    if not scenarios:
        raise ValueError(f"No scenarios in {path}")
    return scenarios


def runbooks_dir(workspace: Workspace) -> Path:
    if workspace.runbooks_dir is None:
        raise WorkspaceError(
            f"no runbooks directory in {workspace.root}. See `diag init`."
        )
    return workspace.runbooks_dir


def runbook_files_on_disk(workspace: Workspace) -> set[str]:
    if workspace.runbooks_dir is None:
        return set()
    return {p.name for p in workspace.runbooks_dir.glob("runbook-*.md")}


def scenario_runbook_names(workspace: Workspace) -> set[str]:
    return {s["runbook"] for s in load_scenarios(workspace)}


def read_runbook_text(workspace: Workspace, filename: str) -> str:
    path = runbooks_dir(workspace) / filename
    if not path.is_file():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")


def build_alertmanager_payload(scenario: dict) -> dict:
    """Build a minimal Alertmanager webhook body for /alert."""
    labels = dict(scenario.get("labels") or {})
    annotations = dict(scenario.get("annotations") or {})
    # Redaction probe — must never appear in audit output.
    annotations.setdefault(
        "summary",
        f"Synthetic E2E {scenario['id']} tenant-smoke-test",
    )
    annotations.setdefault(
        "description",
        "probe 550e8400-e29b-41d4-a716-446655440000",
    )
    return {
        "alerts": [
            {
                "status": "firing",
                "labels": labels,
                "annotations": annotations,
            }
        ]
    }


def coverage_gaps(workspace: Workspace) -> tuple[set[str], set[str]]:
    """Return (runbooks_missing_scenarios, scenarios_missing_runbook_file)."""
    on_disk = runbook_files_on_disk(workspace)
    in_yaml = scenario_runbook_names(workspace)
    return on_disk - in_yaml, in_yaml - on_disk
