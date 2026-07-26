"""Self-sufficient host installer: discover -> collect -> generate -> verify.

See ``docs/INSTALL.md`` and ``diag install --help``.
"""
from __future__ import annotations

from .cli import run_install

__all__ = ["run_install"]
