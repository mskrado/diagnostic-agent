"""NIST-aligned audit logging.

Every diagnostic run is appended to a per-day JSONL file with a timestamp, the
full report, and the raw LLM output. This is the auditable record of what the
agent saw and concluded. Tenant identifiers are redacted before writing.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from ..config import settings
from .redact import redact_text

logger = logging.getLogger(__name__)


def write_audit_record(report: dict, llm_raw: str) -> str | None:
    """Append a redacted audit record; return the file path written, or None."""
    try:
        os.makedirs(settings.audit_log_dir, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = os.path.join(settings.audit_log_dir, f"diagnostics-{day}.jsonl")
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_version": _version(),
            "llm_provider": settings.llm_provider,
            "llm_model": (
                settings.ollama_model
                if settings.llm_provider == "ollama"
                else settings.openai_model
            ),
            "report": report,
            "llm_raw": llm_raw,
        }
        line = redact_text(json.dumps(record, default=str))
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        logger.info("audit record written: %s", path)
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
