"""Detect modifications to upstream-owned paths in a client fork."""
from __future__ import annotations

import subprocess
from pathlib import Path

from app.fork.boundary import CLIENT_DIR, UPSTREAM_CLIENT_ALLOWLIST, is_upstream_owned


def find_upstream_drift(repo_root: Path) -> list[str]:
    """Return relative paths under upstream ownership that differ from HEAD.

    Uses ``git diff`` for tracked files and reports untracked upstream paths
    that should not exist in a clean fork (except allowlisted client/ entries).
    """
    repo_root = repo_root.resolve()
    drift: list[str] = []

    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        for line in proc.stdout.splitlines():
            rel = line.strip()
            if rel and is_upstream_owned(rel):
                drift.append(rel)
    except (OSError, subprocess.SubprocessError):
        pass

    try:
        proc = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        for line in proc.stdout.splitlines():
            rel = line.strip()
            if not rel:
                continue
            if rel.startswith(f"{CLIENT_DIR}/") and rel not in UPSTREAM_CLIENT_ALLOWLIST:
                continue  # client-owned untracked files are expected
            if is_upstream_owned(rel):
                drift.append(f"{rel} (untracked)")
    except (OSError, subprocess.SubprocessError):
        pass

    return sorted(set(drift))


def read_upstream_version(client_dir: Path) -> str:
    path = client_dir / ".upstream-version"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()
