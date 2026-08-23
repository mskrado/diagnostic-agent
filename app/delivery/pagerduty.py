"""PagerDuty delivery helpers for routed diagnoses."""
from __future__ import annotations

from ..clients.pagerduty import PagerDutyClient
from ..config import settings
from .redact import redact_text


def _incident_id_from_alert(alert: dict | None) -> str:
    labels = (alert or {}).get("labels") or {}
    annotations = (alert or {}).get("annotations") or {}
    for key in ("pagerduty_incident_id", "incident_id", "pd_incident_id"):
        value = annotations.get(key) or labels.get(key)
        if value:
            return str(value)
    return ""


def _summary(report: dict) -> str:
    route = report.get("route_decision", "report")
    return redact_text(
        f"{report.get('alert_type', 'unknown')} on {report.get('service', 'unknown')} "
        f"({report.get('severity', 'unknown')}) route={route}"
    )


def _note(report: dict, alert: dict | None = None) -> str:
    diagnosis = report.get("diagnosis") or {}
    primary = diagnosis.get("primary_hypothesis") or {}
    cause = primary.get("cause") or diagnosis.get("error") or "No structured diagnosis returned"
    confidence = diagnosis.get("confidence_note") or "unknown"
    blast = ", ".join(report.get("blast_radius") or []) or "none identified"
    alert_summary = ((alert or {}).get("annotations") or {}).get("summary") or "(none)"
    steps = [str(s).strip() for s in (diagnosis.get("suggested_next_steps") or []) if str(s).strip()]
    step_block = "\n".join(f"- {s}" for s in steps[:5]) if steps else "- (none)"
    return redact_text(
        "\n".join(
            [
                _summary(report),
                f"Alert summary: {alert_summary}",
                f"Primary hypothesis: {cause}",
                f"Confidence note: {confidence}",
                f"Blast radius: {blast}",
                "Suggested next steps:",
                step_block,
            ]
        )
    )


def deliver_pagerduty(
    pagerduty: PagerDutyClient,
    report: dict,
    alert: dict | None = None,
) -> dict | None:
    """Trigger or annotate PagerDuty based on the route decision.

    Returns a small audit-friendly payload when a PagerDuty action succeeds:
    {"incident_id": "...", "action": "triggered"|"noted"}
    """
    if not settings.pagerduty_enabled:
        return None

    route = report.get("route_decision", "report")
    diagnosis = report.get("diagnosis") or {}
    confidence_note = str(diagnosis.get("confidence_note") or "").lower()

    if route == "escalate":
        incident_id = pagerduty.create_incident(
            title=_summary(report),
            service=report.get("service", "unknown"),
            severity=report.get("severity", "unknown"),
            details=_note(report, alert),
        )
        if incident_id:
            return {"incident_id": incident_id, "action": "triggered"}
        return None

    existing_id = _incident_id_from_alert(alert)
    if existing_id and confidence_note == "high":
        if pagerduty.add_note(existing_id, _note(report, alert)):
            return {"incident_id": existing_id, "action": "noted"}
    return None
