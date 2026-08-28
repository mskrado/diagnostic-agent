"""Reactive agentic diagnostic tool.

A LangGraph agent that, on a Prometheus/Alertmanager alert, pulls metrics
(Prometheus), logs (Loki) and dependency context, reasons over them with a
local-first LLM, and emits a structured diagnostic report. Hypotheses only --
no auto-remediation.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    # Release stamps pyproject.toml, so the installed distribution is the single
    # source of truth — a literal here silently drifts from the published tag.
    __version__ = version("diagnostic-agent")
except PackageNotFoundError:  # source tree without an install
    __version__ = "unknown"
