"""CLI entry for ``diag upgrade``."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.fork.boundary import CLIENT_DIR
from app.fork.upgrade import run_upgrade


def add_upgrade_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "upgrade",
        help="Fetch and merge an upstream release into this client fork",
    )
    p.add_argument(
        "--target",
        default="",
        help="Tag or ref to merge (default: latest upstream tag on main)",
    )
    p.add_argument(
        "--remote",
        default="upstream",
        help="Git remote name for upstream (default: upstream)",
    )
    p.add_argument(
        "--client-dir",
        default=CLIENT_DIR,
        help=f"Client directory (default: {CLIENT_DIR}/)",
    )
    p.add_argument(
        "--from-pack",
        default="",
        metavar="DIR",
        help="Offline update pack directory (git bundle + wheelhouse)",
    )
    p.add_argument(
        "--skip-drift-check",
        action="store_true",
        help="Skip check for local edits to upstream-owned paths (not recommended)",
    )
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=run_upgrade_cmd)


def run_upgrade_cmd(args: argparse.Namespace) -> int:
    repo_root = Path(__file__).resolve().parent.parent.parent
    pack = Path(args.from_pack) if args.from_pack else None
    return run_upgrade(
        repo_root=repo_root,
        target=args.target,
        remote=args.remote,
        client_dir=Path(args.client_dir),
        from_pack=pack,
        skip_drift_check=args.skip_drift_check,
        dry_run=args.dry_run,
    )
