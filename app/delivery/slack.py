"""Slack delivery for diagnosis summaries and routing traces.

Uses a Slack incoming webhook so hosts can opt in without adding a full Slack
API client. Every outbound text field is passed through redact_text() before the
payload is sent.
"""
from __future__ import annotations

import logging

import httpx

from ..config import settings
from .redact import redact_text

logger = logging.getLogger(__name__)


def _summary_text(report: dict, alert: dict | None = None) -> str:
    service = report.get("service", "unknown")
    alert_type = report.get("alert_type", "unknown")
    severity = report.get("severity", "unknown")
    route = report.get("route_decision", "report")
    diagnosis = report.get("diagnosis") or {}
    primary = diagnosis.get("primary_hypothesis") or {}
    cause = primary.get("cause") or diagnosis.get("error") or "No structured diagnosis returned"
    alert_summary = ((alert or {}).get("annotations") or {}).get("summary") or ""
    parts = [
        f"{alert_type} on {service} ({severity})",
        f"route={route}",
        f"hypothesis={cause}",
    ]
    if alert_summary:
        parts.append(f"summary={alert_summary}")
    return redact_text(" | ".join(parts))


def format_diagnosis_slack(report: dict, alert: dict | None = None) -> dict:
    """Return an incoming-webhook payload with compact reasoning-trace blocks."""
    diagnosis = report.get("diagnosis") or {}
    primary = diagnosis.get("primary_hypothesis") or {}
    cause = primary.get("cause") or diagnosis.get("error") or "No structured diagnosis returned"
    confidence = primary.get("confidence")
    confidence_note = diagnosis.get("confidence_note") or "unknown"
    route = report.get("route_decision", "report")
    severity = report.get("severity", "unknown")
    blast = ", ".join(report.get("blast_radius") or []) or "none identified"
    next_steps = [str(s).strip() for s in (diagnosis.get("suggested_next_steps") or []) if str(s).strip()]
    if not next_steps:
        next_steps = ["(none)"]

    payload = {
        "text": _summary_text(report, alert),
        "username": settings.slack_username,
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": redact_text(f"{report.get('alert_type', 'unknown')} on {report.get('service', 'unknown')}"),
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": redact_text(f"*Severity*\n{severity}")},
                    {"type": "mrkdwn", "text": redact_text(f"*Route*\n{route}")},
                    {"type": "mrkdwn", "text": redact_text(f"*Confidence note*\n{confidence_note}")},
                    {"type": "mrkdwn", "text": redact_text(f"*Confidence*\n{confidence if confidence is not None else '?'}")},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": redact_text(f"*Primary hypothesis*\n{cause}"),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": redact_text(f"*Blast radius*\n{blast}"),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": redact_text("*Suggested next steps*\n" + "\n".join(f"• {step}" for step in next_steps[:5])),
                },
            },
        ],
    }
    if settings.slack_channel:
        payload["channel"] = settings.slack_channel
    return payload


def deliver_slack(report: dict, alert: dict | None = None) -> bool:
    """Send a redacted Slack webhook message. Returns True on success."""
    if not settings.slack_enabled:
        logger.info("slack delivery disabled (AGENT_SLACK_ENABLED=false)")
        return False
    if not settings.slack_webhook_url.strip():
        logger.warning("slack delivery enabled but AGENT_SLACK_WEBHOOK_URL is empty; skipping")
        return False

    payload = format_diagnosis_slack(report, alert)
    try:
        resp = httpx.post(
            settings.slack_webhook_url,
            json=payload,
            timeout=settings.slack_timeout,
        )
        resp.raise_for_status()
        logger.info("diagnostic slack notification sent")
        return True
    except Exception as exc:  # noqa: BLE001 - delivery must never crash diagnosis
        logger.warning("diagnostic slack notification failed: %s", exc)
        return False
