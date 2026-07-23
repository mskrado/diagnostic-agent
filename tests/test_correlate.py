"""Correlate node — structured LLM output handling."""
from __future__ import annotations

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage

from app.graph.nodes import DiagnosticNodes
from app.graph.schema import Diagnosis, Hypothesis


def _nodes_with_llm(llm) -> DiagnosticNodes:
    return DiagnosticNodes(
        prom=MagicMock(),
        loki=MagicMock(),
        grafana=MagicMock(),
        dep_map=MagicMock(),
        rag=MagicMock(),
        llm=llm,
    )


def test_correlate_structured_output_success():
    diagnosis = Diagnosis(
        primary_hypothesis=Hypothesis(
            cause="DB pool exhausted",
            confidence=85,
            evidence="db_pool_pending=12",
        ),
        secondary_hypotheses=[],
        blast_radius_assessment="platform-service only",
        suggested_next_steps=["Check HikariCP metrics"],
        confidence_note="high",
    )
    llm = MagicMock()
    llm.invoke.return_value = {
        "parsed": diagnosis,
        "raw": AIMessage(
            content='{"primary_hypothesis":{}}',
            usage_metadata={
                "input_tokens": 111,
                "output_tokens": 22,
                "total_tokens": 133,
            },
        ),
    }
    nodes = _nodes_with_llm(llm)
    state = {
        "alert_type": "HighErrorRate",
        "service": "platform-service",
        "severity": "warning",
        "module_hint": "",
        "dependencies": [],
        "prom_data": {},
        "loki_logs": ["HikariPool: Connection is not available"],
        "rag_context": "pool exhaustion runbook",
        "blast_radius": [],
    }
    out = nodes.correlate(state)
    assert out["hypotheses"]["primary_hypothesis"]["cause"] == "DB pool exhausted"
    assert out["llm_raw"] == '{"primary_hypothesis":{}}'
    assert "diagnostic agent" in out["llm_system_prompt"].lower()
    assert "HighErrorRate" in out["llm_user_prompt"]
    assert "pool exhaustion runbook" in out["llm_user_prompt"]
    assert out["llm_token_usage"]["input_tokens"] == 111
    assert out["llm_token_usage"]["output_tokens"] == 22

    reported = nodes.report(out)
    exchange = reported["report"]["llm_exchange"]
    assert exchange["rag_used"] is True
    assert exchange["rag_context"] == "pool exhaustion runbook"
    assert exchange["token_usage"]["total_tokens"] == 133
    assert exchange["user_prompt"] == out["llm_user_prompt"]
    assert exchange["chat_provider"]
    assert exchange["chat_model"]
    assert exchange["embed_provider"]
    assert exchange["embed_model"]


def test_correlate_structured_output_parse_failure():
    llm = MagicMock()
    llm.invoke.return_value = {
        "parsed": None,
        "raw": AIMessage(content="not valid"),
        "parsing_error": ValueError("bad schema"),
    }
    nodes = _nodes_with_llm(llm)
    state = {
        "alert_type": "HighErrorRate",
        "service": "platform-service",
        "severity": "warning",
        "module_hint": "",
        "dependencies": [],
        "prom_data": {},
        "loki_logs": [],
        "rag_context": "",
        "blast_radius": [],
    }
    out = nodes.correlate(state)
    assert "error" in out["hypotheses"]
    assert out["hypotheses"]["raw"] == "not valid"
