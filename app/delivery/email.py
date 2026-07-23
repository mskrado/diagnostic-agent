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
    """Plain-text lines for issue_categories (cause, evidence, tools, fixes)."""
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
        tools = [str(t).strip() for t in (item.get("tool_run_examples") or []) if str(t).strip()]
        if tools:
            line += "\n      tool runs:"
            for t in tools:
                line += f"\n        $ {t}"
        fixes = [str(f).strip() for f in (item.get("fix_suggestions") or []) if str(f).strip()]
        if fixes:
            line += "\n      fixes:"
            for f in fixes:
                line += f"\n        - {f}"
        lines.append(line)
    return lines


def _bullet_block(items: list | None, empty: str = "  - (none)") -> str:
    cleaned = [str(s).strip() for s in (items or []) if str(s).strip()]
    if not cleaned:
        return empty
    return "\n".join(f"  - {s}" for s in cleaned)


def _command_block(items: list | None, empty: str = "  - (none)") -> str:
    cleaned = [str(s).strip() for s in (items or []) if str(s).strip()]
    if not cleaned:
        return empty
    return "\n".join(f"  $ {s}" for s in cleaned)


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


_HTML_FONT = (
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
    "Helvetica,Arial,sans-serif;"
)
_MONO_FONT = "font-family:'SFMono-Regular',Menlo,Consolas,'Courier New',monospace;"


def _severity_color(severity: str) -> str:
    s = (severity or "").lower()
    if s in ("critical", "error", "page", "high"):
        return "#c0392b"
    if s in ("warning", "warn", "medium"):
        return "#d97706"
    return "#2563eb"


def _confidence_style(conf) -> str:
    """Inline style for a confidence badge, colored by risk band."""
    try:
        c = int(conf)
    except (TypeError, ValueError):
        c = 0
    if c >= 80:
        return "background:#fde8e8;color:#b91c1c;"
    if c >= 50:
        return "background:#fef3c7;color:#92400e;"
    return "background:#e0e7ff;color:#3730a3;"


def _section_html(title: str) -> str:
    return (
        f'<div style="{_HTML_FONT}font-size:12px;font-weight:700;color:#111827;'
        "text-transform:uppercase;letter-spacing:.6px;margin:24px 0 12px 0;"
        'padding-bottom:6px;border-bottom:2px solid #eef1f4;">'
        f"{escape(title)}</div>"
    )


def _code_block_html(commands: list[str]) -> str:
    body = "<br/>".join(f"$&nbsp;{escape(c)}" for c in commands)
    return (
        f'<div style="{_MONO_FONT}font-size:12px;line-height:1.6;background:#0f172a;'
        "color:#e2e8f0;padding:10px 12px;border-radius:6px;margin:8px 0 0 0;"
        f'overflow-x:auto;white-space:pre-wrap;word-break:break-word;">{body}</div>'
    )


def _fix_list_html(fixes: list[str]) -> str:
    lis = "".join(
        f'<li style="margin:3px 0;">{escape(f)}</li>' for f in fixes
    )
    return (
        f'<ul style="{_HTML_FONT}font-size:13px;color:#374151;margin:6px 0 0 0;'
        f'padding-left:20px;">{lis}</ul>'
    )


def _category_card_html(item: dict) -> str:
    """One styled card per distinct problem (evidence, next step, tools, fixes)."""
    label = str(item.get("category") or "uncategorized")
    conf = item.get("confidence", "?")
    cause = str(item.get("cause") or "")
    evidence = str(item.get("evidence") or "").strip()
    nxt = str(item.get("suggested_next_step") or "").strip()
    tools = [str(t).strip() for t in (item.get("tool_run_examples") or []) if str(t).strip()]
    fixes = [str(f).strip() for f in (item.get("fix_suggestions") or []) if str(f).strip()]

    card = (
        '<div style="border:1px solid #e5e7eb;border-radius:8px;padding:14px 16px;'
        'margin:0 0 12px 0;background:#ffffff;">'
        '<div style="margin:0 0 8px 0;">'
        f'<span style="{_HTML_FONT}display:inline-block;padding:2px 8px;border-radius:4px;'
        "font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.4px;"
        f'background:#eef2ff;color:#3730a3;">{escape(label)}</span>'
        f'<span style="{_HTML_FONT}display:inline-block;margin-left:6px;padding:2px 8px;'
        f'border-radius:4px;font-size:11px;font-weight:700;{_confidence_style(conf)}">'
        f"{escape(str(conf))}%</span>"
        "</div>"
        f'<div style="{_HTML_FONT}font-size:14px;font-weight:600;color:#111827;'
        f'margin:0 0 8px 0;">{escape(cause)}</div>'
    )
    if evidence:
        card += (
            f'<div style="{_HTML_FONT}font-size:12px;color:#374151;background:#f9fafb;'
            "border-left:3px solid #d1d5db;padding:6px 10px;margin:0 0 8px 0;"
            f'word-break:break-word;"><b>Evidence:</b> {escape(evidence)}</div>'
        )
    if nxt:
        card += (
            f'<div style="{_HTML_FONT}font-size:13px;color:#374151;margin:0 0 4px 0;">'
            f"<b>Next step:</b> {escape(nxt)}</div>"
        )
    if tools:
        card += _code_block_html(tools)
    if fixes:
        card += (
            f'<div style="{_HTML_FONT}font-size:13px;color:#374151;margin-top:8px;">'
            f"<b>Fixes:</b>{_fix_list_html(fixes)}</div>"
        )
    card += "</div>"
    return card


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
    steps = []
    tool_examples: list[str] = []
    fix_suggestions: list[str] = []
    if isinstance(diagnosis, dict):
        steps = diagnosis.get("suggested_next_steps") or []
        tool_examples = diagnosis.get("tool_run_examples") or []
        fix_suggestions = diagnosis.get("fix_suggestions") or []
    if not isinstance(steps, list):
        steps = []
    if not isinstance(tool_examples, list):
        tool_examples = []
    if not isinstance(fix_suggestions, list):
        fix_suggestions = []
    steps_block = _bullet_block(steps)
    tools_block = _command_block(tool_examples)
    fixes_block = _bullet_block(fix_suggestions)

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
        # issue_categories are the source of truth: they already carry each
        # problem's evidence, next step, tools and fixes, so we skip the
        # redundant global secondary/next-steps/tools/fixes sections below.
        plain_parts.extend(["", "Issue categories (per distinct problem):", *categories])
    else:
        if secondary:
            plain_parts.extend(["", "Secondary hypotheses:", *secondary])
    if judge_lines:
        plain_parts.extend(["", *judge_lines])
    plain_parts.extend(["", f"Blast radius: {blast}"])
    if not categories:
        plain_parts.extend(
            [
                "",
                "Suggested next steps (investigate):",
                steps_block,
                "",
                "Tool run examples (copy-paste; human-run):",
                tools_block,
                "",
                "Fix suggestions (human-run; agent does not auto-remediate):",
                fixes_block,
            ]
        )
    plain_parts.extend(
        [
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
            "Hypotheses + guidance only — no auto-remediation.",
        ]
    )
    plain = redact_text("\n".join(plain_parts))

    models = _resolve_models(report)
    sev_color = _severity_color(severity)

    # --- Header banner ---
    header_html = (
        f'<td style="background:{sev_color};padding:22px 28px;">'
        f'<div style="{_HTML_FONT}color:rgba(255,255,255,.85);font-size:11px;'
        'font-weight:700;text-transform:uppercase;letter-spacing:1px;">'
        "Diagnostic report</div>"
        f'<div style="{_HTML_FONT}color:#ffffff;font-size:20px;font-weight:700;'
        f'margin-top:4px;">{escape(str(alert_type))} '
        f'<span style="font-weight:400;">on</span> {escape(str(service))}</div>'
        f'<div style="{_HTML_FONT}margin-top:10px;"><span style="display:inline-block;'
        "padding:3px 10px;border-radius:999px;background:rgba(255,255,255,.22);"
        'color:#ffffff;font-size:11px;font-weight:700;text-transform:uppercase;'
        f'letter-spacing:.5px;">{escape(str(severity))}</span></div>'
        "</td>"
    )

    # --- Models chips ---
    def _chip(label: str, value: str) -> str:
        return (
            f'<span style="{_HTML_FONT}display:inline-block;font-size:12px;color:#374151;'
            'background:#f3f4f6;border-radius:6px;padding:5px 10px;margin:0 6px 6px 0;">'
            f'<b style="color:#6b7280;font-weight:600;">{escape(label)}:</b> '
            f"{escape(value)}</span>"
        )

    models_html = (
        _section_html("Models")
        + "<div>"
        + _chip(
            "Diagnosis",
            _format_provider_model(models.get("chat_provider"), models.get("chat_model")),
        )
        + _chip(
            "Embeddings",
            _format_provider_model(models.get("embed_provider"), models.get("embed_model")),
        )
    )
    judge_models = report.get("judge_models") or (report.get("judge") or {}).get("models")
    if isinstance(judge_models, dict) and (
        judge_models.get("chat_model") or judge_models.get("chat_provider")
    ):
        models_html += _chip(
            "Judge",
            _format_provider_model(
                judge_models.get("chat_provider"), judge_models.get("chat_model")
            ),
        )
    models_html += "</div>"

    # --- Primary hypothesis card ---
    if isinstance(diagnosis, dict) and diagnosis.get("error"):
        primary_html = (
            _section_html("Diagnosis")
            + f'<div style="{_HTML_FONT}font-size:14px;color:#b91c1c;">'
            f"Diagnosis unavailable: {escape(str(diagnosis['error']))}</div>"
        )
    elif cause:
        primary_html = _section_html("Primary hypothesis") + (
            '<div style="border:1px solid #fca5a5;border-left:4px solid '
            f"{sev_color};border-radius:8px;padding:14px 16px;background:#fff7f7;\">"
            f'<div style="{_HTML_FONT}"><span style="display:inline-block;padding:2px 8px;'
            f'border-radius:4px;font-size:11px;font-weight:700;{_confidence_style(confidence)}">'
            f"{escape(str(confidence))}%</span></div>"
            f'<div style="{_HTML_FONT}font-size:15px;font-weight:600;color:#111827;'
            f'margin-top:8px;">{escape(str(cause))}</div>'
        )
        if evidence:
            primary_html += (
                f'<div style="{_HTML_FONT}font-size:12px;color:#374151;background:#ffffff;'
                "border-left:3px solid #d1d5db;padding:6px 10px;margin-top:8px;"
                f'word-break:break-word;"><b>Evidence:</b> {escape(str(evidence))}</div>'
            )
        if confidence_note:
            primary_html += (
                f'<div style="{_HTML_FONT}font-size:12px;color:#6b7280;margin-top:8px;">'
                f"Confidence: {escape(str(confidence_note))}</div>"
            )
        primary_html += "</div>"
    else:
        primary_html = (
            _section_html("Primary hypothesis")
            + f'<div style="{_HTML_FONT}font-size:13px;color:#6b7280;">'
            "See audit log (LLM returned no structured cause).</div>"
        )

    # --- Issue category cards (source of truth) ---
    categories_html = ""
    if isinstance(diagnosis, dict) and diagnosis.get("issue_categories"):
        cards = "".join(
            _category_card_html(item)
            for item in diagnosis["issue_categories"]
            if isinstance(item, dict) and item.get("cause")
        )
        if cards:
            categories_html = _section_html("Issue categories (per distinct problem)") + cards

    # --- Fallback global sections (only when no issue categories) ---
    fallback_html = ""
    if not categories:
        if secondary:
            fallback_html += _section_html("Secondary hypotheses") + _fix_list_html(
                [s.strip()[2:] if s.strip().startswith("- ") else s.strip() for s in secondary]
            )
        clean_steps = [str(s).strip() for s in steps if str(s).strip()]
        clean_tools = [str(t).strip() for t in tool_examples if str(t).strip()]
        clean_fixes = [str(f).strip() for f in fix_suggestions if str(f).strip()]
        if clean_steps:
            fallback_html += _section_html("Suggested next steps (investigate)") + _fix_list_html(
                clean_steps
            )
        if clean_tools:
            fallback_html += _section_html(
                "Tool run examples (copy-paste; human-run)"
            ) + _code_block_html(clean_tools)
        if clean_fixes:
            fallback_html += _section_html("Fix suggestions (human-run)") + _fix_list_html(
                clean_fixes
            )

    # --- Judge ---
    judge_html = ""
    if judge:
        correct = judge.get("correct")
        correct_str = "yes" if correct is True else "no" if correct is False else "?"
        correct_color = (
            "#059669" if correct is True else "#b91c1c" if correct is False else "#6b7280"
        )
        score = judge.get("score")
        reason = (judge.get("reason") or "").strip()
        judge_html = (
            _section_html("Judge")
            + f'<div style="{_HTML_FONT}font-size:13px;color:#374151;">'
            f'<b>Score:</b> {escape(str(score if score is not None else "?"))}/5'
            f' &nbsp;&middot;&nbsp; <b>Correct:</b> '
            f'<span style="color:{correct_color};font-weight:600;">{escape(correct_str)}</span></div>'
        )
        if reason:
            judge_html += (
                f'<div style="{_HTML_FONT}font-size:13px;color:#374151;margin-top:6px;">'
                f"{escape(reason)}</div>"
            )

    # --- Blast radius ---
    blast_html = (
        _section_html("Blast radius")
        + f'<div style="{_HTML_FONT}font-size:13px;color:#374151;">{escape(blast)}</div>'
    )

    # --- Log source (definition table) ---
    src_rows = ""
    for line in source_lines:
        txt = line.strip()
        if ": " in txt:
            k, v = txt.split(": ", 1)
            src_rows += (
                f'<tr><td style="{_HTML_FONT}color:#6b7280;font-size:12px;'
                'padding:3px 14px 3px 0;white-space:nowrap;vertical-align:top;">'
                f'{escape(k)}</td><td style="{_MONO_FONT}font-size:12px;color:#111827;'
                f'word-break:break-all;">{escape(v)}</td></tr>'
            )
        else:
            src_rows += (
                f'<tr><td colspan="2" style="{_HTML_FONT}font-size:12px;color:#111827;">'
                f"{escape(txt)}</td></tr>"
            )
    log_source_html = (
        _section_html("Log source")
        + f'<table style="border-collapse:collapse;width:100%;">{src_rows}</table>'
    )

    # --- Recent logs ---
    log_rows = "".join(
        f'<div style="{_MONO_FONT}font-size:11px;line-height:1.5;color:#334155;'
        "border-bottom:1px solid #f1f5f9;padding:6px 8px;word-break:break-word;\">"
        f"{escape(line.strip()[2:] if line.strip().startswith('- ') else line.strip())}</div>"
        for line in log_lines
    )
    logs_html = (
        _section_html("Recent error/warn logs")
        + '<div style="border:1px solid #eef1f4;border-radius:8px;overflow:hidden;">'
        + log_rows
        + "</div>"
    )

    footer_html = (
        f'<div style="{_HTML_FONT}font-size:11px;color:#9ca3af;margin-top:24px;'
        'padding-top:14px;border-top:1px solid #eef1f4;">'
        f"RAG used: {'yes' if rag_used else 'no'} &nbsp;&middot;&nbsp; "
        "Hypotheses + guidance only — no auto-remediation.</div>"
    )

    body_inner = (
        '<td style="padding:24px 28px;">'
        + models_html
        + primary_html
        + categories_html
        + fallback_html
        + judge_html
        + blast_html
        + log_source_html
        + logs_html
        + footer_html
        + "</td>"
    )

    html = redact_text(
        "<html><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        f'<body style="margin:0;padding:0;background:#f4f5f7;">'
        f'<div style="{_HTML_FONT}display:none;max-height:0;overflow:hidden;">'
        f"{escape(str(alert_type))} on {escape(str(service))} — "
        f"{escape(alert_summary or 'diagnostic report')}</div>"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#f4f5f7;padding:24px 0;"><tr><td align="center">'
        '<table role="presentation" width="680" cellpadding="0" cellspacing="0" '
        'style="max-width:680px;width:100%;background:#ffffff;border-radius:10px;'
        'overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08);">'
        f"<tr>{header_html}</tr>"
        f'<tr><td style="padding:18px 28px 0 28px;">'
        f'<div style="{_HTML_FONT}font-size:14px;color:#111827;">'
        f'<b>Alert summary:</b> {escape(alert_summary or "(none)")}</div></td></tr>'
        f"<tr>{body_inner}</tr>"
        "</table></td></tr></table></body></html>"
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
