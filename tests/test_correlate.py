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
        "raw": AIMessage(content='{"primary_hypothesis":{}}'),
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
    assert out["hypotheses"]["primary_hypothesis"]["cause"] == "DB pool exhausted"
    assert out["llm_raw"] == '{"primary_hypothesis":{}}'


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
