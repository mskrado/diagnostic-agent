"""End-to-end runbook scenario runner for a diagnostic-agent workspace.

Uses a published (or local) agent image's ``diag`` CLI against a host workspace
that contains ``scenarios.yaml`` / runbooks.

Usage (from diagnostic-agent repo root):
  python scripts/runbook-e2e.py -w /path/to/host/workspace
  python scripts/runbook-e2e.py -w ./examples/hello-world --mode offline
  python scripts/runbook-e2e.py -w /path/to/ws --mode live --scenario high-error-rate
  python scripts/runbook-e2e.py -w /path/to/ws --mode all --url http://localhost:8001

Env:
  DIAGNOSTIC_AGENT_IMAGE   default image (ghcr.io/mskrado/diagnostic-agent:latest)
  AGENT_E2E_URL            default live URL (http://localhost:8001)
  AGENT_E2E_WORKSPACE      default -w if unset on CLI

See docs/TESTING.md.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_IMAGE = os.environ.get(
    "DIAGNOSTIC_AGENT_IMAGE", "ghcr.io/mskrado/diagnostic-agent:latest"
)
DEFAULT_URL = os.environ.get("AGENT_E2E_URL", "http://localhost:8001")


def _docker_diag(image: str, workspace: Path, *args: str) -> int:
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{workspace.resolve()}:/workspace:ro",
        image,
        "diag",
        *args,
    ]
    print("+", " ".join(cmd), flush=True)
    return subprocess.call(cmd)


def _docker_diag_e2e(image: str, workspace: Path, url: str, only: str) -> int:
    # Live e2e needs network access to the host agent.
    cmd = [
        "docker",
        "run",
        "--rm",
        "--add-host=host.docker.internal:host-gateway",
        "-v",
        f"{workspace.resolve()}:/workspace:ro",
        image,
        "diag",
        "e2e",
        "--url",
        url,
    ]
    if only:
        cmd.extend(["--only", only])
    print("+", " ".join(cmd), flush=True)
    return subprocess.call(cmd)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "-w",
        "--workspace",
        default=os.environ.get("AGENT_E2E_WORKSPACE", ""),
        help="Host workspace directory (agent.yaml + scenarios.yaml). "
        "Or set AGENT_E2E_WORKSPACE.",
    )
    ap.add_argument(
        "--mode",
        choices=("offline", "live", "all"),
        default="all",
        help="offline = diag validate+lint; live = diag e2e; all = both",
    )
    ap.add_argument(
        "--image",
        default=DEFAULT_IMAGE,
        help=f"Agent image (default: {DEFAULT_IMAGE})",
    )
    ap.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"Running agent base URL for live mode (default: {DEFAULT_URL})",
    )
    ap.add_argument(
        "--scenario",
        default="",
        help="Comma-separated scenario id(s) for live mode (default: all)",
    )
    args = ap.parse_args(argv)

    if not args.workspace:
        print(
            "ERROR: workspace required (-w / --workspace or AGENT_E2E_WORKSPACE)",
            file=sys.stderr,
        )
        return 2

    workspace = Path(args.workspace)
    if not workspace.is_dir():
        print(f"ERROR: workspace missing: {workspace}", file=sys.stderr)
        return 2

    rc = 0
    if args.mode in ("offline", "all"):
        # validate and lint as separate calls for clearer logs
        rc = _docker_diag(args.image, workspace, "validate") or rc
        rc = _docker_diag(args.image, workspace, "lint") or rc

    if args.mode in ("live", "all"):
        url = args.url
        # From inside a container, localhost is the container itself.
        if "localhost" in url or "127.0.0.1" in url:
            url = url.replace("localhost", "host.docker.internal").replace(
                "127.0.0.1", "host.docker.internal"
            )
        live_rc = _docker_diag_e2e(args.image, workspace, url, args.scenario)
        if live_rc != 0:
            print(
                "\nLive e2e failed. Is the agent up and reachable at "
                f"{args.url}?\n"
                "  See docs/TESTING.md",
                file=sys.stderr,
            )
        rc = live_rc or rc

    return rc


if __name__ == "__main__":
    sys.exit(main())
