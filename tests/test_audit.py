"""Audit JSONL includes llm_exchange (prompts + tokens) for RAG/cost eval."""
from __future__ import annotations

import json
from pathlib import Path

from app.delivery.audit import write_audit_record


def test_audit_record_includes_llm_exchange(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.delivery.audit.settings.audit_log_dir", str(tmp_path)
    )
    monkeypatch.setattr(
        "app.delivery.audit.settings.chat_provider", "openai"
    )
    monkeypatch.setattr(
        "app.delivery.audit.settings.chat_model", "gpt-4o-mini"
    )
    monkeypatch.setattr(
        "app.delivery.audit.settings.embed_provider", "openai"
    )
    monkeypatch.setattr(
        "app.delivery.audit.settings.embed_model", "text-embedding-3-small"
    )

    report = {
        "service": "platform-service",
        "alert_type": "HighErrorRate",
        "diagnosis": {"primary_hypothesis": {"cause": "pool"}},
        "llm_context": {
            "rag_context": "Hikari pool exhaustion runbook chunk",
            "rag_used": True,
        },
        "llm_exchange": {
            "system_prompt": "You are a diagnostic agent",
            "user_prompt": "Alert: HighErrorRate on platform-service\nRunbook: none",
            "token_usage": {
                "input_tokens": 1200,
                "output_tokens": 180,
                "total_tokens": 1380,
                "source": "usage_metadata",
            },
        },
    }
    path = write_audit_record(report, llm_raw='{"primary_hypothesis":{}}')
    assert path is not None
    line = Path(path).read_text(encoding="utf-8").strip()
    record = json.loads(line)

    assert "llm_exchange" in record
    assert record["llm_exchange"]["system_prompt"].startswith("You are")
    assert "HighErrorRate" in record["llm_exchange"]["user_prompt"]
    assert "rag_context" not in record["llm_exchange"]
    assert record["llm_context"]["rag_used"] is True
    assert "Hikari" in record["llm_context"]["rag_context"]
    assert record["llm_exchange"]["token_usage"]["input_tokens"] == 1200
    assert record["llm_exchange"]["token_usage"]["output_tokens"] == 180
    assert record["llm_raw"] == '{"primary_hypothesis":{}}'
    # Mirrored under report as well
    assert record["report"]["llm_exchange"]["token_usage"]["total_tokens"] == 1380


def test_audit_redacts_tenant_in_prompts(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.delivery.audit.settings.audit_log_dir", str(tmp_path)
    )
    report = {
        "llm_exchange": {
            "system_prompt": "sys",
            "user_prompt": "Denied for tenant-alpha uuid 550e8400-e29b-41d4-a716-446655440000",
            "rag_context": "",
            "rag_used": False,
            "token_usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }
    }
    path = write_audit_record(report, "")
    line = Path(path).read_text(encoding="utf-8")
    assert "tenant-alpha" not in line
    assert "550e8400" not in line
    assert "tenant-[REDACTED]" in line
    assert "[UUID-REDACTED]" in line
