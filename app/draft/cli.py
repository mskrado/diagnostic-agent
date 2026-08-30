"""``diag draft`` — write the workspace files the evidence supports."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_OUT = "diag-draft"


def add_draft_parser(sub: argparse._SubParsersAction) -> None:
    draft = sub.add_parser(
        "draft",
        help="Draft workspace files from live evidence, verifying each value",
        description=(
            "Scans the stack, proposes workspace configuration, and checks every "
            "proposal against the live stack before writing it. Values that do "
            "not verify are written commented out with the reason. Writes to a "
            "staging directory unless --in-place is given."
        ),
    )
    draft.add_argument(
        "-w",
        "--workspace",
        default=None,
        metavar="PATH",
        help="Workspace to read URLs from (and to write into with --in-place)",
    )
    draft.add_argument(
        "--out",
        default="",
        metavar="DIR",
        help=f"Staging directory for the draft (default: ./{DEFAULT_OUT})",
    )
    draft.add_argument(
        "--in-place",
        action="store_true",
        help="Write into the resolved workspace instead of a staging directory",
    )
    draft.add_argument(
        "--force",
        action="store_true",
        help="Allow overwriting files that already exist",
    )
    draft.add_argument(
        "--bundle",
        default="",
        metavar="PATH",
        help="Reuse a scan bundle (diag scan --out) instead of scanning again",
    )
    draft.add_argument("--prometheus-url", default="", help="Override AGENT_PROMETHEUS_URL")
    draft.add_argument("--loki-url", default="", help="Override AGENT_LOKI_URL")
    draft.add_argument(
        "--alertmanager-url", default="", help="Alertmanager base URL (optional)"
    )
    draft.add_argument("--timeout", type=float, default=10.0)
    draft.add_argument(
        "--window", default="5m", help="Metrics window used when testing templates"
    )
    draft.add_argument(
        "--lookback-minutes",
        type=int,
        default=60,
        help="Log window for sampling and for verifying selectors (default: 60)",
    )
    draft.add_argument("--sample-lines", type=int, default=300)
    draft.add_argument("--max-services", type=int, default=12)
    draft.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be written without writing it",
    )
    draft.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print the decision record as JSON instead of the report",
    )
    draft.set_defaults(func=run_draft)


def run_draft(args: argparse.Namespace) -> int:
    from ..cli import _apply_workspace_env, _package_version
    from ..scan.models import BundleError, ScanEvidence
    from ..workspace import load as load_workspace
    from .plan import DraftOptions, draft, report, scan_for_draft

    ws = load_workspace(args.workspace)
    _apply_workspace_env(ws)

    from .. import config as config_mod

    config_mod.settings = config_mod.Settings()
    settings = config_mod.settings

    options = DraftOptions(
        prometheus_url=args.prometheus_url or settings.prometheus_url,
        loki_url=args.loki_url or settings.loki_url,
        alertmanager_url=args.alertmanager_url,
        timeout=args.timeout,
        lookback_minutes=args.lookback_minutes,
        sample_lines=args.sample_lines,
        max_services=args.max_services,
        window=args.window,
        workspace=str(ws.root),
        agent_version=_package_version(),
    )

    if args.bundle:
        try:
            payload = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
            evidence = ScanEvidence.from_dict(payload)
        except (OSError, ValueError, BundleError) as exc:
            print(f"ERROR: cannot read bundle {args.bundle}: {exc}", file=sys.stderr)
            return 2
    else:
        evidence = scan_for_draft(options)

    if not evidence.prometheus.reachable:
        print(
            "ERROR: Prometheus is unreachable, so nothing can be verified. "
            f"Tried {options.prometheus_url}.",
            file=sys.stderr,
        )
        return 2

    result = draft(evidence, options)

    # With --json, stdout carries only the decision record so it can be piped;
    # progress goes to stderr.
    log = sys.stderr if args.as_json else sys.stdout
    if args.as_json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        print(report(result, evidence))

    target = _target_dir(args, ws)
    print("", file=log)
    written, blocked = _write(
        result, target, dry_run=args.dry_run, force=args.force, log=log
    )

    if blocked:
        print(
            f"\nERROR: {len(blocked)} file(s) already exist in {target}. "
            "Re-run with --force to overwrite, or --out to stage elsewhere:",
            file=sys.stderr,
        )
        for path in blocked:
            print(f"  {path}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(
            f"\ndry run: {len(written)} file(s) would be written to {target}", file=log
        )
        return 0

    print(f"\nwrote {len(written)} file(s) to {target}", file=log)
    print(
        "Review the diff, then validate it: "
        f"diag validate -w {target} && diag lint -w {target}",
        file=log,
    )
    return 0


def _target_dir(args: argparse.Namespace, ws) -> Path:
    if args.in_place:
        return Path(ws.root)
    return Path(args.out or DEFAULT_OUT).expanduser()


def _write(result, target: Path, *, dry_run: bool, force: bool, log=None):
    """Write every drafted file, or none of them.

    The existence check runs over the whole set before anything is written, so a
    collision half way through cannot leave a partially drafted workspace.
    """
    written: list[Path] = []
    blocked: list[Path] = []

    for drafted in result.files:
        path = target / drafted.path
        if path.exists() and not force:
            blocked.append(path)
            continue
        written.append(path)

    if blocked or dry_run:
        return written, blocked

    for drafted in result.files:
        path = target / drafted.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(drafted.content, encoding="utf-8", newline="\n")
        print(f"  {path}", file=log or sys.stdout)
    return written, blocked
