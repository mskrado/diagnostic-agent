"""Assembles the LangGraph diagnostic graph from injected collaborators."""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from .nodes import DiagnosticNodes
from .routing import should_route
from .state import DiagnosticState


def build_diagnostic_graph(nodes: DiagnosticNodes):
    graph = StateGraph(DiagnosticState)
    graph.add_node("detect", nodes.detect)
    graph.add_node("retrieve", nodes.retrieve)
    graph.add_node("rag_lookup", nodes.rag_lookup)
    graph.add_node("correlate", nodes.correlate)
    graph.add_node("report", nodes.report)
    graph.add_node("execute_runbook", nodes.execute_runbook)

    graph.set_entry_point("detect")
    graph.add_edge("detect", "retrieve")
    graph.add_edge("retrieve", "rag_lookup")
    graph.add_edge("rag_lookup", "correlate")
    graph.add_edge("correlate", "report")
    graph.add_conditional_edges(
        "report",
        should_route,
        {
            "report": END,
            "escalate": END,
            "execute": "execute_runbook",
        },
    )
    # TODO(#53): graph.add_edge("execute_runbook", "verify")
    graph.add_edge("execute_runbook", END)
    return graph.compile()
