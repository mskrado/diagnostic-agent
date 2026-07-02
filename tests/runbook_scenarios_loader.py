"""Load and validate runbook_scenarios.yaml for offline and live E2E tests."""
from __future__ import annotations

from pathlib import Path

import yaml

_PKG_ROOT = Path(__file__).resolve().parent.parent
_SCENARIOS_PATH = _PKG_ROOT / "runbook_scenarios.yaml"
_RUNBOOKS_DIR = _PKG_ROOT / "runbooks"


def scenarios_path() -> Path:
    return _SCENARIOS_PATH


def runbooks_dir() -> Path:
    return _RUNBOOKS_DIR


def load_scenarios() -> list[dict]:
    raw = yaml.safe_load(_SCENARIOS_PATH.read_text(encoding="utf-8"))
    scenarios = raw.get("scenarios") or []
    if not scenarios:
        raise ValueError(f"No scenarios in {_SCENARIOS_PATH}")
    return scenarios


def runbook_files_on_disk() -> set[str]:
    return {p.name for p in _RUNBOOKS_DIR.glob("runbook-*.md")}


def scenario_runbook_names() -> set[str]:
    return {s["runbook"] for s in load_scenarios()}


def read_runbook_text(filename: str) -> str:
    path = _RUNBOOKS_DIR / filename
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


def coverage_gaps() -> tuple[set[str], set[str]]:
    """Return (runbooks_missing_scenarios, scenarios_missing_runbook_file)."""
    on_disk = runbook_files_on_disk()
    in_yaml = scenario_runbook_names()
    return on_disk - in_yaml, in_yaml - on_disk
