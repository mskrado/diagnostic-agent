"""Ownership boundary between upstream product code and client deployment."""
from __future__ import annotations

from pathlib import Path

CLIENT_DIR = "client"

# Paths upstream may change on every release. Clients must not edit these.
UPSTREAM_OWNED_PATHS: frozenset[str] = frozenset(
    {
        "app",
        "runbooks",
        "examples",
        "eval",
        "tests",
        "docs",
        "scripts",
        ".github",
        "Dockerfile",
        "pyproject.toml",
        "requirements.txt",
        "requirements-dev.txt",
        "requirements.lock",
        ".gitignore",
        ".env.example",
        "README.md",
        "LICENSE",
        "CODE_OF_CONDUCT.md",
        "CONTRIBUTING.md",
    }
)

# The only tracked files upstream may ship under client/.
UPSTREAM_CLIENT_ALLOWLIST: frozenset[str] = frozenset(
    {
        f"{CLIENT_DIR}/README.md",
        f"{CLIENT_DIR}/.gitkeep",
    }
)


def is_upstream_owned(rel_path: str) -> bool:
    """Return True if *rel_path* is owned by upstream (not client/)."""
    normalized = rel_path.replace("\\", "/").lstrip("./")
    if normalized.startswith(f"{CLIENT_DIR}/"):
        return normalized in UPSTREAM_CLIENT_ALLOWLIST
    top = normalized.split("/", 1)[0]
    return top in UPSTREAM_OWNED_PATHS or normalized in UPSTREAM_OWNED_PATHS


def client_owned_paths(repo_root: Path) -> list[str]:
    """List tracked paths under client/ that are client-owned (not allowlisted)."""
    client_root = repo_root / CLIENT_DIR
    if not client_root.is_dir():
        return []
    owned: list[str] = []
    for path in sorted(client_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(repo_root).as_posix()
        if rel not in UPSTREAM_CLIENT_ALLOWLIST:
            owned.append(rel)
    return owned


def upstream_client_entries(repo_root: Path) -> list[str]:
    """Tracked or present files directly under client/ (for guard tests)."""
    client_root = repo_root / CLIENT_DIR
    if not client_root.is_dir():
        return []
    return sorted(
        p.relative_to(repo_root).as_posix()
        for p in client_root.iterdir()
        if p.is_file()
    )
