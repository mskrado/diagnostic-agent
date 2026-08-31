"""``diag drift`` — report what no longer matches the workspace."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def add_drift_parser(sub: argparse._SubParsersAction) -> None:
    drift = sub.add_parser(
        "drift",
        help="Report workspace drift against a live stack or scan bundle",
        description=(
            "Re-scans Prometheus/Loki (or reads a scan bundle) and reports new "
            "services without map nodes, map nodes that went dark, alerts "
            "without runbooks, and metrics templates that stopped returning "
            "data. Exit code 1 on error-class drift."
        ),
    )
    drift.add_argument(
        "-w",
        "--workspace",
        default=None,
        metavar="PATH",
        help="Workspace to compare against (required for a meaningful check)",
    )
    drift.add_argument(
        "--bundle",
        default="",
        metavar="PATH",
        help="Reuse a diag scan --out bundle instead of scanning again",
    )
    drift.add_argument("--prometheus-url", default="", help="Override AGENT_PROMETHEUS_URL")
    drift.add_argument("--loki-url", default="", help="Override AGENT_LOKI_URL")
    drift.add_argument("--alertmanager-url", default="", help="Alertmanager base URL")
    drift.add_argument("--timeout", type=float, default=10.0)
    drift.add_argument("--lookback-minutes", type=int, default=60)
    drift.add_argument("--window", default="5m")
    drift.add_argument(
        "--out",
        default="",
        metavar="PATH",
        help="Write the drift report as JSON",
    )
    drift.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print the drift report as JSON",
    )
    drift.add_argument(
        "--no-oracle",
        action="store_true",
        help="Skip live PromQL/LogQL checks even when URLs are available",
    )
    drift.set_defaults(func=run_drift)


def run_drift(args: argparse.Namespace) -> int:
    from ..cli import _apply_workspace_env
    from ..scan.collect import ScanOptions, collect_evidence
    from ..scan.models import BundleError, ScanEvidence
    from ..workspace import load as load_workspace
    from .detect import detect
    from .report import render

    ws = load_workspace(args.workspace)
    _apply_workspace_env(ws)

    from .. import config as config_mod

    config_mod.settings = config_mod.Settings()
    settings = config_mod.settings

    prometheus_url = args.prometheus_url or settings.prometheus_url
    loki_url = args.loki_url or settings.loki_url

    if args.bundle:
        try:
            payload = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
            evidence = ScanEvidence.from_dict(payload)
        except (OSError, ValueError, BundleError) as exc:
            print(f"ERROR: cannot read bundle {args.bundle}: {exc}", file=sys.stderr)
            return 2
    else:
        if not prometheus_url:
            print(
                "ERROR: no Prometheus URL and no --bundle. Pass --prometheus-url "
                "or --bundle PATH.",
                file=sys.stderr,
            )
            return 2
        evidence = collect_evidence(
            ScanOptions(
                prometheus_url=prometheus_url,
                loki_url=loki_url,
                alertmanager_url=args.alertmanager_url,
                timeout=args.timeout,
                lookback_minutes=args.lookback_minutes,
                workspace=str(ws.root),
            )
        )

    oracle = None
    if not args.no_oracle:
        prom = prometheus_url or evidence.prometheus.url
        loki = loki_url or evidence.loki.url
        if prom:
            from ..draft.verify import LiveOracle

            oracle = LiveOracle(
                prom,
                loki,
                timeout=args.timeout,
                lookback_minutes=args.lookback_minutes,
            )

    report = detect(evidence, ws, oracle=oracle, window=args.window)

    if args.as_json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(render(report))

    if args.out:
        Path(args.out).write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        dest = sys.stderr if args.as_json else sys.stdout
        print(f"\nwrote {args.out}", file=dest)

    return 0 if report.ok else 1
