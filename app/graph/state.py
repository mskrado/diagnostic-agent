"""Shared state object passed between LangGraph nodes."""
from __future__ import annotations

from typing import TypedDict


class DiagnosticState(TypedDict, total=False):
    # --- input (from the alert) ---
    service: str          # resolved service name (e.g. platform-service)
    alert_type: str       # alertname (e.g. HighErrorRate)
    severity: str
    severity_normalized: str
    raw_labels: dict
    module_hint: str      # logical module guessed from the alert, if any

    # --- retrieve ---
    dependencies: list[str]
    blast_radius: list[str]
    prom_data: dict       # per-service metric snapshot
    loki_logs: list[str]  # formatted error/warn lines with timestamp + trace_id
    log_source: dict      # Loki query metadata for email/audit

    # --- rag ---
    rag_context: str

    # --- correlate ---
    hypotheses: dict      # parsed JSON from the LLM
    llm_raw: str          # raw LLM text (kept for audit)
    llm_system_prompt: str
    llm_user_prompt: str
    llm_token_usage: dict  # input/output/total when provider reports them
    route: str             # report | escalate | execute

    # --- execution (Track B) ---
    matched_action: dict       # {"runbook": str, "action_id": str, "params": dict}
    classifier_verdict: dict   # ClassifierVerdict as a dict
    execution_result: dict     # ActionResult as a dict
    outcome: str               # resolved | escalated | failed

    # --- report ---
    report: dict          # final structured report
