"""SMTP email delivery for diagnostic reports (hypotheses + evidence).

Separate from Alertmanager's alert email: this message is sent after the agent
finishes correlating metrics/logs and includes the structured diagnosis.
"""
from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

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
    log_services = log_source.get("log_services")
    if isinstance(log_services, list) and log_services:
        lines.append(f"  Log services: {', '.join(str(s) for s in log_services)}")
    line_filter = log_source.get("line_filter")
    if line_filter:
        lines.append(f"  Line filter: {line_filter}")
    return lines


def _format_log_sample(logs: list[str]) -> list[str]:
    """Redacted log lines for the email body (capped)."""
    if not logs:
        return ["  (none — Promtail/Loki may be down or no ERROR/WARN lines in window)"]
    redacted = redact_log_lines([str(line) for line in logs[:_MAX_LOG_LINES_IN_EMAIL]])
    return [f"  - {line}" for line in redacted]


def _resolve_models(report: dict) -> dict:
    """Prefer report.models, then llm_exchange snapshot fields, then settings."""
    models = report.get("models")
    if isinstance(models, dict) and (
        models.get("chat_model") or models.get("chat_provider")
    ):
        return models
    exchange = report.get("llm_exchange") or {}
    if isinstance(exchange, dict) and (
        exchange.get("chat_model") or exchange.get("chat_provider")
    ):
        return {
            "chat_provider": exchange.get("chat_provider"),
            "chat_model": exchange.get("chat_model"),
            "embed_provider": exchange.get("embed_provider"),
            "embed_model": exchange.get("embed_model"),
        }
    return settings.model_snapshot()


def _format_provider_model(provider: str | None, model: str | None) -> str:
    p = (provider or "").strip() or "?"
    m = (model or "").strip() or "?"
    return f"{p} / {m}"


def _models_plain_lines(report: dict) -> list[str]:
    models = _resolve_models(report)
    lines = [
        "Models:",
        f"  Diagnosis: {_format_provider_model(models.get('chat_provider'), models.get('chat_model'))}",
        f"  Embeddings: {_format_provider_model(models.get('embed_provider'), models.get('embed_model'))}",
    ]
    judge_models = report.get("judge_models") or (report.get("judge") or {}).get("models")
    if isinstance(judge_models, dict) and (
        judge_models.get("chat_model") or judge_models.get("chat_provider")
    ):
        lines.append(
            "  Judge: "
            + _format_provider_model(
                judge_models.get("chat_provider"),
                judge_models.get("chat_model"),
            )
        )
    return lines


def _format_issue_categories(diagnosis: dict) -> list[str]:
    """Plain-text lines for issue_categories (cause, confidence, evidence, next)."""
    lines: list[str] = []
    for item in diagnosis.get("issue_categories") or []:
        if not isinstance(item, dict) or not item.get("cause"):
            continue
        label = item.get("category") or "uncategorized"
        conf = item.get("confidence", "?")
        line = f"  - [{label}] {item['cause']} ({conf}%)"
        evidence = (item.get("evidence") or "").strip()
        if evidence:
            line += f"\n      evidence: {evidence}"
        nxt = (item.get("suggested_next_step") or "").strip()
        if nxt:
            line += f"\n      next: {nxt}"
        lines.append(line)
    return lines


def _extract_judge(report: dict) -> dict | None:
    """Normalize optional judge payload from report (eval / experimental fields)."""
    judge = report.get("judge")
    if isinstance(judge, dict) and any(
        k in judge for k in ("score", "judge_score", "correct", "judge_correct", "reason", "judge_reason")
    ):
        return {
            "score": judge.get("score", judge.get("judge_score")),
            "correct": judge.get("correct", judge.get("judge_correct")),
            "reason": judge.get("reason", judge.get("judge_reason")) or "",
        }
    if report.get("judge_score") is not None or report.get("judge_reason"):
        return {
            "score": report.get("judge_score"),
            "correct": report.get("judge_correct"),
            "reason": report.get("judge_reason") or "",
        }
    return None


def _judge_plain_lines(judge: dict) -> list[str]:
    score = judge.get("score")
    correct = judge.get("correct")
    reason = (judge.get("reason") or "").strip()
    correct_str = (
        "yes" if correct is True else "no" if correct is False else "?"
    )
    lines = [
        "Judge:",
        f"  Score: {score if score is not None else '?'}/5",
        f"  Correct: {correct_str}",
    ]
    if reason:
        lines.append(f"  Reason: {reason}")
    return lines


def _html_list_from_multiline(items: list[str]) -> str:
    """Turn plain '  - ...' blocks (possibly multi-line) into <li> entries."""
    parts: list[str] = []
    for item in items:
        body = item.strip()
        if body.startswith("- "):
            body = body[2:]
        parts.append(f"<li>{escape(body).replace(chr(10), '<br/>')}</li>")
    return "".join(parts)


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
    confidence_note = (
        diagnosis.get("confidence_note") if isinstance(diagnosis, dict) else None
    )

    if isinstance(diagnosis, dict) and diagnosis.get("error"):
        hypothesis_block = f"Diagnosis unavailable: {diagnosis['error']}"
    elif cause:
        hypothesis_block = f"Primary hypothesis ({confidence}%): {cause}"
        if evidence:
            hypothesis_block += f"\nEvidence: {evidence}"
        if confidence_note:
            hypothesis_block += f"\nConfidence note: {confidence_note}"
    else:
        hypothesis_block = "Primary hypothesis: see audit log (LLM returned no structured cause)"

    secondary = []
    if isinstance(diagnosis, dict):
        for item in diagnosis.get("secondary_hypotheses") or []:
            if isinstance(item, dict) and item.get("cause"):
                conf = item.get("confidence", "?")
                secondary.append(f"  - {item['cause']} ({conf}%)")

    categories = _format_issue_categories(diagnosis) if isinstance(diagnosis, dict) else []
    models_lines = _models_plain_lines(report)
    judge = _extract_judge(report)
    judge_lines = _judge_plain_lines(judge) if judge else []

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
        *models_lines,
        "",
        hypothesis_block,
    ]
    if categories:
        plain_parts.extend(["", "Issue categories (per distinct problem):", *categories])
    if secondary:
        plain_parts.extend(["", "Secondary hypotheses:", *secondary])
    if judge_lines:
        plain_parts.extend(["", *judge_lines])
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

    source_pre = escape("\n".join(line.strip() for line in source_lines))
    logs_pre = escape("\n".join(line.strip() for line in log_lines))
    steps_pre = escape(steps_block)
    models = _resolve_models(report)
    models_html = (
        "<h3>Models</h3><ul>"
        f"<li><b>Diagnosis:</b> {escape(_format_provider_model(models.get('chat_provider'), models.get('chat_model')))}</li>"
        f"<li><b>Embeddings:</b> {escape(_format_provider_model(models.get('embed_provider'), models.get('embed_model')))}</li>"
    )
    judge_models = report.get("judge_models") or (report.get("judge") or {}).get("models")
    if isinstance(judge_models, dict) and (
        judge_models.get("chat_model") or judge_models.get("chat_provider")
    ):
        models_html += (
            f"<li><b>Judge:</b> {escape(_format_provider_model(judge_models.get('chat_provider'), judge_models.get('chat_model')))}</li>"
        )
    models_html += "</ul>"

    judge_html = ""
    if judge:
        correct = judge.get("correct")
        correct_str = (
            "yes" if correct is True else "no" if correct is False else "?"
        )
        score = judge.get("score")
        reason = (judge.get("reason") or "").strip()
        judge_html = (
            "<h3>Judge</h3>"
            f"<p><b>Score:</b> {escape(str(score if score is not None else '?'))}/5"
            f" &nbsp;|&nbsp; <b>Correct:</b> {escape(correct_str)}</p>"
        )
        if reason:
            judge_html += f"<p>{escape(reason)}</p>"

    html = redact_text(
        "<html><body>"
        f"<h2>Diagnostic: {escape(str(alert_type))} on {escape(str(service))}</h2>"
        f"<p><b>Severity:</b> {escape(str(severity))}</p>"
        f"<p><b>Alert summary:</b> {escape(alert_summary or '(none)')}</p>"
        + models_html
        + f"<p>{escape(hypothesis_block).replace(chr(10), '<br/>')}</p>"
        + (
            "<h3>Issue categories (per distinct problem)</h3><ul>"
            + _html_list_from_multiline(categories)
            + "</ul>"
            if categories
            else ""
        )
        + (
            "<h3>Secondary hypotheses</h3><ul>"
            + "".join(
                f"<li>{escape(s.strip()[2:] if s.strip().startswith('- ') else s.strip())}</li>"
                for s in secondary
            )
            + "</ul>"
            if secondary
            else ""
        )
        + judge_html
        + f"<p><b>Blast radius:</b> {escape(blast)}</p>"
        f"<h3>Suggested next steps (read-only)</h3><pre>{steps_pre}</pre>"
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
