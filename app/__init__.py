"""Publishi.ai reactive agentic diagnostic tool.

A LangGraph agent that, on a Prometheus/Alertmanager alert, pulls metrics
(Prometheus), logs (Loki) and dependency context, reasons over them with a
local-first LLM, and emits a structured diagnostic report. Hypotheses only --
no auto-remediation.
"""

__version__ = "0.1.0"
