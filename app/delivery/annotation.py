"""Grafana annotation delivery.

Posts a compact, tenant-redacted summary of the diagnosis as a Grafana
annotation so the report surfaces on dashboards at the incident timestamp.
"""
from __future__ import annotations

import logging

from ..clients.grafana import GrafanaClient
from ..config import settings
from .redact import redact_text

logger = logging.getLogger(__name__)


def deliver_annotation(grafana: GrafanaClient, report: dict) -> bool:
    if not settings.grafana_annotations_enabled:
        return False

    diagnosis = report.get("diagnosis", {})
    primary = diagnosis.get("primary_hypothesis", {}) if isinstance(diagnosis, dict) else {}
    cause = primary.get("cause", "see audit log")
    confidence = primary.get("confidence", "?")

    service = report.get("service", "unknown")
    alert = report.get("alert_type", "unknown")
    blast = ", ".join(report.get("blast_radius", []) or []) or "none identified"

    text = redact_text(
        f"<b>Diagnostic: {alert} on {service}</b><br/>"
        f"Primary hypothesis ({confidence}%): {cause}<br/>"
        f"Blast radius: {blast}"
    )
    tags = ["diagnostic-agent", str(service), str(alert)]
    return grafana.post_annotation(text=text, tags=tags)
