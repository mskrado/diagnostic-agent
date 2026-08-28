"""Upstream must ship an empty client/ directory so merges never conflict."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.fork.boundary import UPSTREAM_CLIENT_ALLOWLIST, is_upstream_owned

REPO_ROOT = Path(__file__).resolve().parent.parent


def _tracked_under_client() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "client"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git unavailable")
    return [line for line in out.stdout.splitlines() if line.strip()]


def test_upstream_ships_empty_client_directory():
    tracked = _tracked_under_client()
    assert tracked, "client/ must exist in the repo"
    extras = set(tracked) - UPSTREAM_CLIENT_ALLOWLIST
    assert not extras, (
        "Upstream must not commit client deployment files — only README.md and "
        f".gitkeep. Found: {sorted(extras)}. Clients scaffold via `diag init`."
    )


def test_is_upstream_owned_classification():
    assert is_upstream_owned("app/cli.py")
    assert is_upstream_owned("runbooks/runbook-high-error-rate.md")
    assert is_upstream_owned("client/README.md")
    assert is_upstream_owned("client/.gitkeep")
    assert not is_upstream_owned("client/workspace/agent.yaml")
    assert not is_upstream_owned("client/agent/docker-compose.yml")
