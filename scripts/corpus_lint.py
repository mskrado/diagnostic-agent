"""Offline corpus lint — no LLM credentials required.

Checks that every runbook has a scenario (and vice versa), and that blind-eval
cases' must_reference tokens appear in the injected logs.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _load_yaml(path: Path):
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def check_runbook_scenarios() -> list[str]:
    errors: list[str] = []
    on_disk = {p.name for p in (ROOT / "runbooks").glob("runbook-*.md")}
    scenarios = _load_yaml(ROOT / "runbook_scenarios.yaml")
    in_yaml = {
        s["runbook"]
        for s in scenarios.get("scenarios") or []
        if isinstance(s, dict) and s.get("runbook")
    }
    missing = sorted(on_disk - in_yaml)
    extra = sorted(in_yaml - on_disk)
    if missing:
        errors.append(f"runbooks without scenarios: {missing}")
    if extra:
        errors.append(f"scenarios without runbooks: {extra}")
    return errors


def check_blind_eval_grounding() -> list[str]:
    errors: list[str] = []
    data = _load_yaml(ROOT / "eval" / "blind_eval_dataset.yaml")
    for case in data.get("cases") or []:
        cid = case.get("id", "?")
        logs = "\n".join(case.get("logs") or []).lower()
        expected = case.get("expected") or {}
        for token in expected.get("must_reference") or []:
            if str(token).lower() not in logs:
                errors.append(
                    f"blind eval {cid}: must_reference token {token!r} not found in logs"
                )
    return errors


_AUTO_REMEDIATE = re.compile(
    r"\b(auto-?remediat|automatically (fix|restart|scale|delete|kill))\b",
    re.IGNORECASE,
)


def check_hypotheses_only() -> list[str]:
    errors: list[str] = []
    for path in (ROOT / "runbooks").glob("runbook-*.md"):
        text = path.read_text(encoding="utf-8")
        if "Hypotheses-only" not in text and "hypotheses only" not in text.lower():
            errors.append(f"{path.relative_to(ROOT)}: missing Hypotheses-only section")
        if _AUTO_REMEDIATE.search(text) and "Do NOT auto-remediate" not in text:
            errors.append(
                f"{path.relative_to(ROOT)}: possible auto-remediation wording without disclaimer"
            )
    return errors


def main() -> int:
    errors: list[str] = []
    errors.extend(check_runbook_scenarios())
    errors.extend(check_blind_eval_grounding())
    errors.extend(check_hypotheses_only())
    if errors:
        print("corpus lint FAILED:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("corpus lint OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
