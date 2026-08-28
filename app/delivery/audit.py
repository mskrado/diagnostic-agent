"""NIST-aligned audit logging.

Every diagnostic run is appended to a per-day JSONL file with a timestamp, the
full report, the exact LLM system/user prompts, retrieval context, token usage,
and the raw LLM output (including Bedrock ToolUse args). This is the auditable
record of what the agent saw and concluded. Tenant identifiers are redacted
before writing.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from ..config import settings
from .redact import redact_text

logger = logging.getLogger(__name__)


def _llm_context_from_report(report: dict) -> dict[str, Any]:
    """Prefer report.llm_context; fall back to legacy llm_exchange RAG fields."""
    ctx = report.get("llm_context")
    if isinstance(ctx, dict):
        return {
            "rag_context": ctx.get("rag_context") or "",
            "rag_used": bool(ctx.get("rag_used")),
        }
    exchange = report.get("llm_exchange") or {}
    return {
        "rag_context": exchange.get("rag_context") or "",
        "rag_used": bool(exchange.get("rag_used")),
    }


def build_audit_record(report: dict, llm_raw: str) -> dict[str, Any]:
    """Build the pre-redaction audit object (also used for email attachments).

    Top-level fields mirror the report for easy jq / attachment access:

    - ``llm_raw`` — full model output (text and/or ToolUse JSON)
    - ``llm_context`` — retrieval context only (RAG)
    - ``llm_exchange`` — prompts + token usage + model ids
    """
    exchange = report.get("llm_exchange") or {}
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_version": _version(),
        "chat_provider": settings.chat_provider,
        "chat_model": settings.chat_model,
        "embed_provider": settings.embed_provider,
        "embed_model": settings.embed_model,
        "llm_exchange": exchange,
        "llm_context": _llm_context_from_report(report),
        "llm_raw": llm_raw,
        "report": report,
    }


def redact_audit_json(record: dict[str, Any]) -> str:
    """Serialize and redact an audit record for disk / email."""
    return redact_text(json.dumps(record, default=str))


def write_audit_record(report: dict, llm_raw: str) -> str | None:
    """Append a redacted audit record; return the file path written, or None."""
    try:
        os.makedirs(settings.audit_log_dir, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = os.path.join(settings.audit_log_dir, f"diagnostics-{day}.jsonl")
        record = build_audit_record(report, llm_raw)
        line = redact_audit_json(record)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        usage = (record.get("llm_exchange") or {}).get("token_usage") or {}
        llm_context = record.get("llm_context") or {}
        logger.info(
            "audit record written: %s tokens_in=%s tokens_out=%s tokens_total=%s "
            "rag_used=%s llm_raw_chars=%d",
            path,
            usage.get("input_tokens"),
            usage.get("output_tokens"),
            usage.get("total_tokens"),
            llm_context.get("rag_used"),
            len(llm_raw or ""),
        )
        return path
    except OSError as exc:
        logger.error("failed to write audit record: %s", exc)
        return None


def _version() -> str:
    try:
        from .. import __version__

        return __version__
    except Exception:  # noqa: BLE001
        return "unknown"
