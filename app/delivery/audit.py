"""NIST-aligned audit logging.

Every diagnostic run is appended to a per-day JSONL file with a timestamp, the
full report, the exact LLM system/user prompts (incl. RAG context), token usage,
and the raw LLM output. This is the auditable record of what the agent saw and
concluded. Tenant identifiers are redacted before writing.
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


def build_audit_record(report: dict, llm_raw: str) -> dict[str, Any]:
    """Build the pre-redaction audit object (also used for email attachments)."""
    exchange = report.get("llm_exchange") or {}
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_version": _version(),
        "chat_provider": settings.chat_provider,
        "chat_model": settings.chat_model,
        "embed_provider": settings.embed_provider,
        "embed_model": settings.embed_model,
        # Easy retrieval: prompts + tokens at the top level
        "llm_exchange": exchange,
        "llm_raw": llm_raw,
        "report": report,
    }


def redact_audit_json(record: dict[str, Any]) -> str:
    """Serialize and redact an audit record for disk / email."""
    return redact_text(json.dumps(record, default=str))


def write_audit_record(report: dict, llm_raw: str) -> str | None:
    """Append a redacted audit record; return the file path written, or None.

    Top-level ``llm_exchange`` mirrors ``report.llm_exchange`` so prompts and
    tokens are easy to pull with jq without walking the full report tree.
    """
    try:
        os.makedirs(settings.audit_log_dir, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = os.path.join(settings.audit_log_dir, f"diagnostics-{day}.jsonl")
        record = build_audit_record(report, llm_raw)
        line = redact_audit_json(record)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        usage = (record.get("llm_exchange") or {}).get("token_usage") or {}
        logger.info(
            "audit record written: %s tokens_in=%s tokens_out=%s tokens_total=%s "
            "rag_used=%s",
            path,
            usage.get("input_tokens"),
            usage.get("output_tokens"),
            usage.get("total_tokens"),
            (record.get("llm_exchange") or {}).get("rag_used"),
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
