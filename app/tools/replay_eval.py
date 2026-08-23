"""Offline replay harness for routing decisions.

This evaluator is intentionally lightweight and deterministic: it replays
host-owned alert scenarios through the same severity normalization + route
decision logic used by the graph, without needing live Prometheus/Loki/LLM
backends. Known-incident runbook expectations come from scenarios.yaml.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .. import config as config_mod
from ..graph.routing import normalize_severity, should_route
from ..workspace import Workspace, WorkspaceError
from ..workspace import load as load_workspace

_RESULTS_DIRNAME = "replay-results"


def load_cases(path: Path) -> list[dict]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cases = raw.get("scenarios") or raw.get("cases") or []
    if not cases:
        raise ValueError(f"No replay cases in {path}")
    return cases


def expected_route(case: dict) -> str:
    replay = case.get("replay") or {}
    route = replay.get("expected_route")
    if route:
        return str(route)
    severity = str((case.get("labels") or case.get("alert") or {}).get("severity", "")).lower()
    return "escalate" if severity == "critical" else "report"


def expected_runbook(case: dict) -> str:
    replay = case.get("replay") or {}
    if replay.get("expected_runbook"):
        return str(replay["expected_runbook"])
    return case.get("runbook", "") if expected_route(case) == "execute" else ""


def confidence_note(case: dict) -> str:
    replay = case.get("replay") or {}
    if replay.get("confidence_note"):
        return str(replay["confidence_note"])
    return "high" if expected_route(case) == "execute" else "medium"


def replay_case(case: dict) -> dict:
    labels = dict(case.get("labels") or case.get("alert") or {})
    selected_runbook = case.get("runbook", "") if confidence_note(case) == "high" else ""
    state = {
        "severity": labels.get("severity"),
        "severity_normalized": normalize_severity(labels.get("severity")),
        "hypotheses": {"confidence_note": confidence_note(case)},
        "rag_context": f"selected runbook: {selected_runbook}" if selected_runbook else "",
    }
    route = should_route(state)
    actual_runbook = selected_runbook if route == "execute" else ""
    exp_route = expected_route(case)
    exp_runbook = expected_runbook(case)
    return {
        "id": case.get("id"),
        "route_expected": exp_route,
        "route_actual": route,
        "runbook_expected": exp_runbook,
        "runbook_actual": actual_runbook,
        "passed": route == exp_route and actual_runbook == exp_runbook,
    }


def main(argv: list[str] | None = None, *, workspace: Workspace | None = None) -> int:
    ws = workspace or load_workspace()
    ap = argparse.ArgumentParser(
        prog="diag replay",
        description=(
            "Offline replay harness: assert routing decisions and known-incident "
            "runbook selection from workspace scenarios.yaml."
        ),
    )
    ap.add_argument(
        "--dataset",
        default=str(ws.scenarios_path) if ws.scenarios_path else None,
        metavar="PATH",
        help="Replay dataset YAML (default: workspace scenarios file)",
    )
    ap.add_argument(
        "--out",
        default=str(ws.root / _RESULTS_DIRNAME),
        metavar="DIR",
        help=f"Directory for result JSON files (default: <workspace>/{_RESULTS_DIRNAME})",
    )
    ap.add_argument(
        "--only",
        default="",
        metavar="IDS",
        help="Comma-separated case id(s) to replay (default: all cases)",
    )
    args = ap.parse_args(argv)
    if not args.dataset:
        raise WorkspaceError(
            f"no scenarios file in {ws.root} (expected scenarios.yaml). See `diag init`."
        )

    cases = load_cases(Path(args.dataset))
    if args.only:
        wanted = {c.strip() for c in args.only.split(",") if c.strip()}
        cases = [c for c in cases if c.get("id") in wanted]
        missing = wanted - {c.get("id") for c in cases}
        if missing:
            print(f"WARNING: unknown --only id(s): {sorted(missing)}")

    # Replay specifically tests the routed graph, so force the feature flag on.
    original = config_mod.settings.routing_enabled
    config_mod.settings.routing_enabled = True
    try:
        results = [replay_case(case) for case in cases]
    finally:
        config_mod.settings.routing_enabled = original

    failures = [r for r in results if not r["passed"]]
    for row in results:
        if row["passed"]:
            print(f"ok   {row['id']}: route={row['route_actual']} runbook={row['runbook_actual'] or '(none)'}")
        else:
            print(
                f"FAIL {row['id']}: route={row['route_actual']} expected={row['route_expected']}; "
                f"runbook={row['runbook_actual'] or '(none)'} expected={row['runbook_expected'] or '(none)'}"
            )

    summary = {
        "cases": len(results),
        "passed": len(results) - len(failures),
        "failed": len(failures),
        "pass_rate": round((len(results) - len(failures)) / (len(results) or 1), 3),
    }
    print("\nSummary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"replay-eval-{stamp}.json"
    out_path.write_text(
        json.dumps({"summary": summary, "results": results}, indent=2), encoding="utf-8"
    )
    print(f"\nWrote {out_path}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
