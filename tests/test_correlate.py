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
    ctx = reported["report"]["llm_context"]
    assert ctx["rag_used"] is True
    assert ctx["rag_context"] == "pool exhaustion runbook"
    assert "rag_context" not in exchange
    assert exchange["token_usage"]["total_tokens"] == 133
    assert exchange["user_prompt"] == out["llm_user_prompt"]
    assert exchange["chat_provider"]
    assert exchange["chat_model"]
    assert exchange["embed_provider"]
    assert exchange["embed_model"]
    assert reported["report"]["models"]["chat_model"] == exchange["chat_model"]


def test_correlate_tooluse_empty_content_stores_args_in_llm_raw():
    diagnosis = Diagnosis(
        primary_hypothesis=Hypothesis(
            cause="upstream timeout",
            confidence=70,
            evidence="read timeout",
        ),
        secondary_hypotheses=[],
        blast_radius_assessment="api-gateway",
        suggested_next_steps=["Check platform-service latency"],
        confidence_note="medium",
    )
    llm = MagicMock()
    llm.invoke.return_value = {
        "parsed": diagnosis,
        "raw": AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "Diagnosis",
                    "args": {
                        "primary_hypothesis": {
                            "cause": "upstream timeout",
                            "confidence": 70,
                        }
                    },
                    "id": "call-1",
                    "type": "tool_call",
                }
            ],
            usage_metadata={
                "input_tokens": 100,
                "output_tokens": 77,
                "total_tokens": 177,
            },
        ),
    }
    nodes = _nodes_with_llm(llm)
    out = nodes.correlate(
        {
            "alert_type": "GatewayHighErrorRate",
            "service": "api-gateway",
            "severity": "warning",
            "module_hint": "",
            "dependencies": [],
            "prom_data": {},
            "loki_logs": [],
            "rag_context": "",
            "blast_radius": [],
        }
    )
    assert out["llm_raw"]
    assert "upstream timeout" in out["llm_raw"]
    assert out["llm_token_usage"]["output_tokens"] == 77


def test_correlate_structured_output_parse_failure(monkeypatch):
    import app.llm as llm_mod

    llm = MagicMock()
    llm.invoke.return_value = {
        "parsed": None,
        "raw": AIMessage(content="not valid"),
        "parsing_error": ValueError("bad schema"),
    }
    # Retry path also fails so correlate surfaces the unavailable-diagnosis error.
    fallback = MagicMock()
    fallback.invoke.return_value = AIMessage(content="still not valid")
    monkeypatch.setattr(llm_mod, "get_chat_model", lambda **kwargs: fallback)

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
    assert out["hypotheses"]["raw"] == "still not valid"
    fallback.invoke.assert_called_once()
