"""Deterministic workspace drafting from live-stack evidence.

``diag scan`` reports what a stack exposes; ``diag draft`` turns that into the
workspace files it implies. Nothing here calls an LLM, and nothing is written
that the stack itself did not confirm — unconfirmed proposals land in the file
commented out, next to the reason they failed.
"""
from __future__ import annotations

from .models import Candidate, DraftedFile, DraftResult
from .plan import DraftOptions, draft, report, scan_for_draft

__all__ = [
    "Candidate",
    "DraftedFile",
    "DraftOptions",
    "DraftResult",
    "draft",
    "report",
    "scan_for_draft",
]
