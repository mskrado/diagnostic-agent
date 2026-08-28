"""Detect modifications to upstream-owned paths in a client fork."""
from __future__ import annotations

import subprocess
from pathlib import Path

from app.fork.boundary import CLIENT_DIR, UPSTREAM_CLIENT_ALLOWLIST, is_upstream_owned


class DriftCheckError(RuntimeError):
    """Raised when drift could not be determined (no git, not a repo, ...).

    Swallowing this would report a dirty fork as clean and let ``diag upgrade``
    merge over local edits, so callers must surface it instead.
    """


def _git_lines(repo_root: Path, args: list[str]) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
    except FileNotFoundError as exc:
        raise DriftCheckError("git executable not found on PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip() or f"git {' '.join(args)} failed"
        raise DriftCheckError(detail) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise DriftCheckError(str(exc)) from exc
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def find_upstream_drift(repo_root: Path) -> list[str]:
    """Return relative paths under upstream ownership that differ from HEAD.

    Uses ``git diff`` for tracked files and reports untracked upstream paths
    that should not exist in a clean fork (except allowlisted client/ entries).

    Raises:
        DriftCheckError: if git could not answer the question.
    """
    repo_root = repo_root.resolve()
    drift: list[str] = []

    for rel in _git_lines(repo_root, ["diff", "--name-only", "HEAD"]):
        if is_upstream_owned(rel):
            drift.append(rel)

    for rel in _git_lines(repo_root, ["ls-files", "--others", "--exclude-standard"]):
        if rel.startswith(f"{CLIENT_DIR}/") and rel not in UPSTREAM_CLIENT_ALLOWLIST:
            continue  # client-owned untracked files are expected
        if is_upstream_owned(rel):
            drift.append(f"{rel} (untracked)")

    return sorted(set(drift))


def read_upstream_version(client_dir: Path) -> str:
    path = client_dir / ".upstream-version"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()
