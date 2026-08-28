"""Client-fork ownership boundaries and upgrade helpers."""
from __future__ import annotations

from app.fork.boundary import (
    CLIENT_DIR,
    UPSTREAM_OWNED_PATHS,
    client_owned_paths,
    is_upstream_owned,
    upstream_client_entries,
)

__all__ = [
    "CLIENT_DIR",
    "UPSTREAM_OWNED_PATHS",
    "client_owned_paths",
    "is_upstream_owned",
    "upstream_client_entries",
]
