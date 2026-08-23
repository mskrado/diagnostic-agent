"""Severity normalization and routing decisions for the diagnostic graph.

Track A introduces an explicit routed state machine, but hosts must be able to
keep today's linear behavior until they opt in. This module centralizes the
severity parsing and the route decision so later issues (#52/#53) can reuse the
same logic rather than scattering it across nodes/builders.
"""
from __future__ import annotations

from .state import DiagnosticState
from .. import config as config_mod

_SEVERITY_ALIASES = {
    "sev1": "SEV1",
    "p1": "SEV1",
    "critical": "SEV1",
    "fatal": "SEV1",
    "sev2": "SEV2",
    "p2": "SEV2",
    "error": "SEV2",
    "high": "SEV2",
    "sev3": "SEV3",
    "p3": "SEV3",
    "warning": "SEV3",
    "warn": "SEV3",
    "medium": "SEV3",
    "sev4": "SEV4",
    "p4": "SEV4",
    "info": "SEV4",
    "informational": "SEV4",
    "low": "SEV4",
}

_SEVERITY_RANK = {
    "SEV1": 1,
    "SEV2": 2,
    "SEV3": 3,
    "SEV4": 4,
    "UNKNOWN": 99,
}


def normalize_severity(value: str | None) -> str:
    """Map host-specific severity strings onto SEV1..SEV4 or UNKNOWN."""
    if not value:
        return "UNKNOWN"
    token = str(value).strip().lower()
    return _SEVERITY_ALIASES.get(token, "UNKNOWN")


def should_route(state: DiagnosticState) -> str:
    """Return the next route label for the post-report graph edge.

    - default-safe: when routing is disabled, preserve today's behavior
    - SEV1/SEV2 or low-confidence diagnoses -> escalate
    - SEV3/SEV4 with high confidence and retrieved runbook context -> execute
    - everything else -> report
    """
    if not config_mod.settings.routing_enabled:
        return "report"

    severity = state.get("severity_normalized") or normalize_severity(state.get("severity"))
    if _SEVERITY_RANK.get(severity, 99) <= 2:
        return "escalate"

    confidence = str((state.get("hypotheses") or {}).get("confidence_note", "")).lower()
    if confidence == "low":
        return "escalate"
    if confidence == "high" and bool(state.get("rag_context")):
        return "execute"
    return "report"
