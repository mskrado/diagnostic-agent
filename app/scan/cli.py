"""``diag scan`` — report what the agent can see on a live stack."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def add_scan_parser(sub: argparse._SubParsersAction) -> None:
    scan = sub.add_parser(
        "scan",
        help="Inspect a live stack and report the evidence a workspace needs",
        description=(
            "Read-only inspection of Prometheus, Loki, and Alertmanager: which "
            "services exist, which metric naming convention is in use, which "
            "alerts are defined, and what the logs look like. Writes no "
            "workspace files."
        ),
    )
    scan.add_argument(
        "-w",
        "--workspace",
        default=None,
        metavar="PATH",
        help="Workspace to read URLs and redaction rules from, and to compare "
        "alert coverage against",
    )
    scan.add_argument(
        "--prometheus-url", default="", help="Override AGENT_PROMETHEUS_URL"
    )
    scan.add_argument("--loki-url", default="", help="Override AGENT_LOKI_URL")
    scan.add_argument(
        "--alertmanager-url",
        default="",
        help="Alertmanager base URL (skipped when unset)",
    )
    scan.add_argument(
        "--out",
        default="",
        metavar="PATH",
        help="Write the evidence bundle as JSON. Holds sampled log lines: "
        "gitignore it",
    )
    scan.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print the bundle as JSON instead of the report",
    )
    scan.add_argument("--timeout", type=float, default=10.0)
    scan.add_argument(
        "--lookback-minutes",
        type=int,
        default=60,
        help="Log sample window (default: 60)",
    )
    scan.add_argument(
        "--sample-lines",
        type=int,
        default=300,
        help="Total log lines to sample, spread across streams (default: 300)",
    )
    scan.add_argument(
        "--max-services",
        type=int,
        default=12,
        help="Cap on streams sampled and dependencies probed (default: 12)",
    )
    scan.add_argument(
        "--no-samples",
        action="store_true",
        help="Skip log sampling entirely (no log lines are read)",
    )
    scan.add_argument(
        "--keep-lines",
        action="store_true",
        help="Keep up to 10 scrubbed sample lines per stream in the bundle",
    )
    scan.add_argument(
        "--verbose", action="store_true", help="Show every alert and marker"
    )
    scan.set_defaults(func=run_scan)


def run_scan(args: argparse.Namespace) -> int:
    from ..cli import _apply_workspace_env
    from ..workspace import load as load_workspace
    from .collect import ScanOptions, collect_evidence
    from .report import render

    ws = load_workspace(args.workspace)
    _apply_workspace_env(ws)

    from .. import config as config_mod

    config_mod.settings = config_mod.Settings()
    settings = config_mod.settings

    options = ScanOptions(
        prometheus_url=args.prometheus_url or settings.prometheus_url,
        loki_url=args.loki_url or settings.loki_url,
        alertmanager_url=args.alertmanager_url,
        timeout=args.timeout,
        lookback_minutes=args.lookback_minutes,
        sample_lines=args.sample_lines,
        max_services=args.max_services,
        include_samples=not args.no_samples,
        keep_lines=args.keep_lines,
        workspace=str(ws.root),
    )

    evidence = collect_evidence(options)
    payload = evidence.to_dict()

    if args.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render(evidence, verbose=args.verbose))

    if args.out:
        out_path = Path(args.out).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(f"\nwrote {out_path}")
        if args.keep_lines:
            print(
                "This bundle holds scrubbed log lines. Keep it out of version "
                "control unless you have reviewed it."
            )

    if not evidence.prometheus.reachable:
        print(
            f"\nscan FAILED: Prometheus unreachable at {options.prometheus_url}",
            file=sys.stderr,
        )
        return 1
    print("\nscan OK")
    return 0
