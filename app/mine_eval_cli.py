"""``diag mine-eval`` — draft blind-eval cases from redacted audit logs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def add_mine_eval_parser(sub: argparse._SubParsersAction) -> None:
    mine = sub.add_parser(
        "mine-eval",
        help="Draft blind-eval cases from redacted audit JSONL",
        description=(
            "Reads diagnostic audit logs and drafts candidate blind_eval cases. "
            "Logs are re-scrubbed with built-in patterns. Writes a draft file "
            "by default; never overwrites a curated blind_eval.yaml without "
            "--force."
        ),
    )
    mine.add_argument(
        "--audits",
        nargs="+",
        metavar="PATH",
        required=True,
        help="Audit JSONL file(s) or directories (diagnostics-*.jsonl)",
    )
    mine.add_argument(
        "-o",
        "--out",
        default="blind_eval.draft.yaml",
        metavar="PATH",
        help="Output path (default: ./blind_eval.draft.yaml)",
    )
    mine.add_argument(
        "--force",
        action="store_true",
        help="Allow overwriting an existing output file",
    )
    mine.add_argument("--min-logs", type=int, default=2)
    mine.add_argument("--max-cases", type=int, default=50)
    mine.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print the mined cases as JSON instead of writing YAML",
    )
    mine.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be written without writing it",
    )
    mine.set_defaults(func=run_mine_eval)


def run_mine_eval(args: argparse.Namespace) -> int:
    from .mine_eval import mine_paths, render_dataset

    paths = [Path(p).expanduser() for p in args.audits]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        print(f"ERROR: audit path(s) not found: {', '.join(missing)}", file=sys.stderr)
        return 2

    result = mine_paths(paths, min_logs=args.min_logs, max_cases=args.max_cases)

    print(f"mined {len(result.cases)} case(s); skipped {len(result.skipped)}")
    for warning in result.warnings:
        print(f"warning: {warning}")
    if not result.cases:
        print("nothing to write", file=sys.stderr)
        for skip in result.skipped[:10]:
            print(f"  skip: {skip}", file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps({"cases": result.case_dicts, "skipped": result.skipped}, indent=2))
        return 0

    out = Path(args.out).expanduser()
    if out.exists() and not args.force and not args.dry_run:
        print(
            f"ERROR: {out} already exists. Re-run with --force to overwrite, "
            "or choose a different --out.",
            file=sys.stderr,
        )
        return 1

    content = render_dataset(result)
    if args.dry_run:
        print(f"dry run: would write {len(result.cases)} case(s) to {out}")
        print(f"({len(content)} bytes)")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8", newline="\n")
    print(f"wrote {out}")
    print(
        "Review the cases, then merge into your curated blind_eval.yaml "
        "and point agent.yaml at it."
    )
    return 0
