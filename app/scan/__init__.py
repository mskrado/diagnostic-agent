"""Live-stack evidence collection for workspace authoring.

``diag scan`` answers "what can the agent actually see here?" before anyone
writes a workspace file: which services exist in metrics and logs, which naming
convention the metrics follow, which alerts the rulers define, and what shape
the logs are. It reads only; nothing here writes workspace files.
"""
from __future__ import annotations

from .collect import ScanOptions, collect_evidence
from .models import SCHEMA_VERSION, ScanEvidence

__all__ = ["ScanOptions", "ScanEvidence", "SCHEMA_VERSION", "collect_evidence"]
