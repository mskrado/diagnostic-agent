"""CLI entry for ``diag init`` — scaffold a client deployment under ``client/``."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from app.fork.boundary import CLIENT_DIR

from .cli import _print_next_steps, _run_install_core
from .client_scaffold import scaffold_client_extras


def _git_upstream_version(repo_root: Path) -> str:
    """Best-effort upstream version from git tag or package metadata."""
    try:
        proc = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        tag = proc.stdout.strip()
        if tag:
            return tag.lstrip("v")
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("diagnostic-agent")
    except PackageNotFoundError:
        return "unknown"


def add_init_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "init",
        help=(
            "Discover your stack and scaffold a client deployment under client/ "
            "(compose, workspace, start scripts, docs)"
        ),
    )
    p.add_argument(
        "--output",
        "-o",
        default=CLIENT_DIR,
        metavar="DIR",
        help=f"Client directory (default: {CLIENT_DIR}/)",
    )
    p.add_argument("--target", default="local", help="local (default) or host to probe")
    p.add_argument("--ssh", default=None, metavar="USER@HOST")
    p.add_argument(
        "--preset",
        default="auto",
        choices=["auto", "generic-prometheus", "spring-micrometer"],
    )
    p.add_argument("--prometheus-url", default=None)
    p.add_argument("--loki-url", default=None)
    p.add_argument("--grafana-url", default=None)
    p.add_argument("--alertmanager-url", default=None)
    p.add_argument("--webhook-url", default=None)
    p.add_argument("--chat-provider", default=None)
    p.add_argument("--chat-model", default=None)
    p.add_argument(
        "--agent-image",
        default=None,
        help="Prebuilt image when not building from source (default: GHCR latest)",
    )
    p.add_argument(
        "--base-image",
        default="python:3.12-slim",
        help="Python base image for self-build (default: python:3.12-slim)",
    )
    p.add_argument(
        "--pull-image",
        action="store_true",
        help="Pull prebuilt GHCR image instead of building from repo source",
    )
    p.add_argument("--pip-index-url", default="", help="Internal PyPI mirror for image build")
    p.add_argument("--pip-extra-index-url", default="")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--non-interactive", action="store_true")
    p.add_argument("--allow-degraded", action="store_true")
    p.add_argument("--accept-defaults", action="store_true")
    p.add_argument("--yes", "-y", action="store_true")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--start", action="store_true")
    p.add_argument("--timeout", type=float, default=3.0)
    p.set_defaults(func=run_init)


def run_init(args: argparse.Namespace) -> int:
    repo_root = Path(__file__).resolve().parent.parent.parent
    output = Path(args.output)
    if output.name != CLIENT_DIR and CLIENT_DIR not in output.parts:
        print(
            f"WARNING: output {output} is not under {CLIENT_DIR}/ — "
            "client fork layout expects client/workspace + client/agent",
            file=sys.stderr,
        )

    # Default: build from source (air-gapped / internal mirror friendly).
    build_from_source = not args.pull_image
    agent_image = args.agent_image or "ghcr.io/mskrado/diagnostic-agent:latest"

    extra_overrides = {
        "build_from_source": build_from_source,
        "base_image": args.base_image,
        "pip_index_url": args.pip_index_url,
        "pip_extra_index_url": args.pip_extra_index_url,
        "agent_image": agent_image,
        "local_image_tag": "diagnostic-agent:local",
    }

    rc, output_path, params, report = _run_install_core(
        args,
        output=output,
        layout="client",
        extra_overrides=extra_overrides,
        repo_root=repo_root,
    )
    if rc != 0:
        return rc

    upstream_version = _git_upstream_version(repo_root)
    extras = scaffold_client_extras(
        client_dir=output_path,
        report=report,
        params=params,
        upstream_version=upstream_version,
        repo_root=repo_root,
        dry_run=args.dry_run,
    )
    if not args.dry_run:
        print(f"Scaffolded {len(extras)} client file(s) (scripts, docs, CI)")
        print(f"Upstream version recorded: {upstream_version} -> {output_path / '.upstream-version'}")
        print("\nClient fork next steps")
        print("----------------------")
        print(f"  1. Review  {output_path / 'workspace' / 'service_map.yaml'}")
        print(f"  2. Copy    {output_path / 'agent' / '.env.example'} -> "
              f"{output_path / 'agent' / '.env'}  (if .env missing)")
        print(f"  3. Start   {output_path / 'scripts' / 'start.sh'}")
        print(f"  4. Health  curl -sf http://127.0.0.1:{params.agent_host_port}/health")
        print(f"  5. Wire    merge {output_path / 'observability'} into your live stack")
        print(f"  6. Commit  {output_path}/ to your private repo (never commit .env)")
        print(f"\nFull instructions: {output_path / 'APPLY.md'}")
        print("Upgrades and offline packs: docs/INSTALL.md")
        if report.warnings:
            print("\nWarnings to resolve before the agent can diagnose:")
            for w in report.warnings:
                print(f"  ! {w}")
    else:
        print(f"DRY-RUN would scaffold {len(extras)} client extras")
    return 0
