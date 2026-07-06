"""SMTP email delivery for diagnostic reports (hypotheses + evidence).

Separate from Alertmanager's alert email: this message is sent after the agent
finishes correlating metrics/logs and includes the structured diagnosis.
"""
from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from ..config import settings
from .redact import redact_log_lines, redact_text

logger = logging.getLogger(__name__)

_MAX_LOG_LINES_IN_EMAIL = 10


def _format_log_source(log_source: dict) -> list[str]:
    """Human-readable Loki source lines for the email body."""
    if not log_source:
        return ["  (source not recorded)"]
    system = log_source.get("system") or "loki"
    url = log_source.get("url") or "(unknown)"
    logql = log_source.get("logql") or "(unknown)"
    lookback = log_source.get("lookback_minutes")
    level = log_source.get("level") or "ERROR|WARN"
    svc = log_source.get("service") or "(unknown)"
    lines = [
        f"  System: {system}",
        f"  URL: {url}",
        f"  Service: {svc}",
        f"  Level: {level}",
        f"  LogQL: {logql}",
    ]
    if lookback is not None:
        lines.append(f"  Lookback: {lookback}m")
    return lines


def _format_log_sample(logs: list[str]) -> list[str]:
    """Redacted log lines for the email body (capped)."""
    if not logs:
        return ["  (none — Promtail/Loki may be down or no ERROR/WARN lines in window)"]
    redacted = redact_log_lines([str(line) for line in logs[:_MAX_LOG_LINES_IN_EMAIL]])
    return [f"  - {line}" for line in redacted]


def format_diagnosis_email(report: dict, alert: dict | None = None) -> tuple[str, str, str]:
    """Return (subject, plain_text, html) for a diagnostic report email."""
    service = report.get("service", "unknown")
    alert_type = report.get("alert_type", "unknown")
    severity = report.get("severity", "unknown")
    env_tag = settings.email_subject_prefix.strip() or "publishi"

    subject = redact_text(f"[{env_tag} diagnostic] {alert_type} on {service} ({severity})")

    labels = (alert or {}).get("labels", {})
    annotations = (alert or {}).get("annotations", {})
    alert_summary = annotations.get("summary") or annotations.get("description") or ""
    if not alert_summary and labels:
        alert_summary = f"alertname={labels.get('alertname', alert_type)}"

    diagnosis = report.get("diagnosis", {})
    primary = diagnosis.get("primary_hypothesis", {}) if isinstance(diagnosis, dict) else {}
    cause = primary.get("cause") if isinstance(primary, dict) else None
    confidence = primary.get("confidence", "?") if isinstance(primary, dict) else "?"
    evidence = primary.get("evidence", "") if isinstance(primary, dict) else ""

    if isinstance(diagnosis, dict) and diagnosis.get("error"):
        hypothesis_block = f"Diagnosis unavailable: {diagnosis['error']}"
    elif cause:
        hypothesis_block = f"Primary hypothesis ({confidence}%): {cause}"
        if evidence:
            hypothesis_block += f"\nEvidence: {evidence}"
    else:
        hypothesis_block = "Primary hypothesis: see audit log (LLM returned no structured cause)"

    secondary = []
    if isinstance(diagnosis, dict):
        for item in diagnosis.get("secondary_hypotheses") or []:
            if isinstance(item, dict) and item.get("cause"):
                conf = item.get("confidence", "?")
                secondary.append(f"  - {item['cause']} ({conf}%)")

    blast = ", ".join(report.get("blast_radius", []) or []) or "none identified"
    steps = report.get("diagnosis", {}).get("suggested_next_steps", []) if isinstance(
        diagnosis, dict
    ) else []
    if not isinstance(steps, list):
        steps = []
    steps_block = "\n".join(f"  - {s}" for s in steps if s) or "  - (none)"

    evidence_block = report.get("evidence") or {}
    rag_used = evidence_block.get("rag_used", False)
    metrics = evidence_block.get("metrics") or {}
    log_source = evidence_block.get("log_source") or {}
    log_sample = evidence_block.get("error_log_sample") or []
    source_lines = _format_log_source(log_source if isinstance(log_source, dict) else {})
    log_lines = _format_log_sample(log_sample if isinstance(log_sample, list) else [])

    plain_parts = [
        f"Alert: {alert_type}",
        f"Service: {service}",
        f"Severity: {severity}",
        "",
        "Alert summary:",
        alert_summary or "(none)",
        "",
        hypothesis_block,
    ]
    if secondary:
        plain_parts.extend(["", "Secondary hypotheses:", *secondary])
    plain_parts.extend(
        [
            "",
            f"Blast radius: {blast}",
            "",
            "Suggested next steps (read-only):",
            steps_block,
            "",
            "Log source:",
            *source_lines,
            "",
            "Recent error/warn logs:",
            *log_lines,
            "",
            f"RAG used: {'yes' if rag_used else 'no'}",
            f"Metrics snapshot keys: {', '.join(sorted(metrics.keys())) or 'none'}",
            "",
            "Hypotheses only — no auto-remediation.",
        ]
    )
    plain = redact_text("\n".join(plain_parts))

    source_pre = "\n".join(line.strip() for line in source_lines)
    logs_pre = "\n".join(line.strip() for line in log_lines)
    html = redact_text(
        "<html><body>"
        f"<h2>Diagnostic: {alert_type} on {service}</h2>"
        f"<p><b>Severity:</b> {severity}</p>"
        f"<p><b>Alert summary:</b> {alert_summary or '(none)'}</p>"
        f"<p>{hypothesis_block.replace(chr(10), '<br/>')}</p>"
        + (
            "<h3>Secondary hypotheses</h3><ul>"
            + "".join(f"<li>{s.strip()[2:]}</li>" for s in secondary)
            + "</ul>"
            if secondary
            else ""
        )
        + f"<p><b>Blast radius:</b> {blast}</p>"
        f"<h3>Suggested next steps (read-only)</h3><pre>{steps_block}</pre>"
        f"<h3>Log source</h3><pre>{source_pre}</pre>"
        f"<h3>Recent error/warn logs</h3><pre>{logs_pre}</pre>"
        f"<p><small>RAG used: {'yes' if rag_used else 'no'} | "
        "Hypotheses only — no auto-remediation.</small></p>"
        "</body></html>"
    )
    return subject, plain, html


def deliver_email(report: dict, alert: dict | None = None) -> bool:
    """Send a tenant-redacted diagnostic email. Returns True on success."""
    if not settings.email_enabled:
        logger.info("email delivery disabled (AGENT_EMAIL_ENABLED=false)")
        return False

    recipients = [r.strip() for r in settings.email_to.split(",") if r.strip()]
    if not recipients:
        logger.warning("email delivery enabled but AGENT_EMAIL_TO is empty; skipping")
        return False

    subject, plain, html = format_diagnosis_email(report, alert)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.sendmail(settings.smtp_from, recipients, msg.as_string())
        logger.info("diagnostic email sent to %s", recipients)
        return True
    except OSError as exc:
        logger.warning("diagnostic email failed: %s", exc)
        return False
