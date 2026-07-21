"""LangGraph node implementations.

Graph shape:  detect -> retrieve -> rag_lookup -> correlate -> report -> END

Nodes are bound methods on DiagnosticNodes so they can share injected
collaborators (clients, dependency map, RAG store, LLM) without globals.
"""
from __future__ import annotations

import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage

from ..clients import promql
from ..clients.grafana import GrafanaClient
from ..clients.loki import LokiClient
from ..clients.prometheus import PrometheusClient
from ..config import settings
from ..dependency_map import DependencyMap
from ..llm_usage import extract_token_usage
from ..rag.store import RagStore
from .prompts import SYSTEM_PROMPT
from .state import DiagnosticState

logger = logging.getLogger(__name__)

_MODULE_RE = re.compile(r"c\.p\.([a-z]+)")


class DiagnosticNodes:
    def __init__(
        self,
        prom: PrometheusClient,
        loki: LokiClient,
        grafana: GrafanaClient,
        dep_map: DependencyMap,
        rag: RagStore,
        llm,
    ):
        self.prom = prom
        self.loki = loki
        self.grafana = grafana
        self.dep_map = dep_map
        self.rag = rag
        self.llm = llm

    # ---- detect --------------------------------------------------------
    def detect(self, state: DiagnosticState) -> DiagnosticState:
        labels = state.get("raw_labels", {})
        raw_service = (
            state.get("service")
            or labels.get("service")
            or labels.get("job")
            or "unknown"
        )
        service = self.dep_map.resolve(raw_service)
        module_hint = labels.get("module", "")
        logger.info("detect: service=%s alert=%s", service, state.get("alert_type"))
        return {
            **state,
            "service": service,
            "alert_type": state.get("alert_type") or labels.get("alertname", "unknown"),
            "severity": state.get("severity") or labels.get("severity", "unknown"),
            "module_hint": module_hint,
        }

    # ---- retrieve ------------------------------------------------------
    def retrieve(self, state: DiagnosticState) -> DiagnosticState:
        service = state["service"]
        window = settings.metrics_window
        dependencies = self.dep_map.neighbours(service)
        blast_radius = self.dep_map.blast_radius(service)

        prom_data: dict = {}
        for svc in [service] + dependencies:
            snapshot: dict = {}
            kind = self.dep_map.kind(svc)
            if kind in ("gateway", "monolith", "unknown"):
                snapshot["error_rate"] = self.prom.instant(promql.error_rate(svc, window))
                snapshot["request_rate"] = self.prom.instant(promql.request_rate(svc, window))
                snapshot["latency_p99"] = self.prom.instant(promql.latency_p99(svc, window))
                snapshot["up"] = self.prom.instant(promql.service_up(svc))
                snapshot["heap_used_ratio"] = self.prom.instant(
                    promql.jvm_heap_used_ratio(svc)
                )
            probe = promql.DEPENDENCY_PROBES.get(kind)
            if probe is not None:
                snapshot[f"{kind}_probe"] = self.prom.instant(probe(service))
            prom_data[svc] = {k: v for k, v in snapshot.items() if v is not None}

        # Module-aware DB pool signal (drives the most common incident type).
        prom_data.setdefault(service, {})
        pending = self.prom.instant(promql.db_pool_pending(service))
        if pending is not None:
            prom_data[service]["db_pool_pending"] = pending

        # Error/warn logs for the affected service (Spring Boot JSON).
        # Include WARN so smoke tests and soft failures (e.g. S3 health) appear
        # in diagnostic emails when no ERROR lines exist yet.
        logql = f'{{service="{service}"}} | json | level=~"ERROR|WARN"'
        lookback = settings.loki_lookback_minutes
        raw_entries = self.loki.query_range(
            logql,
            lookback_minutes=lookback,
            limit=settings.loki_limit,
        )
        messages = self.loki.format_log_entries(raw_entries)[:20]
        log_source = {
            "system": "loki",
            "url": settings.loki_url,
            "logql": logql,
            "lookback_minutes": lookback,
            "level": "ERROR|WARN",
            "service": service,
        }

        # Refine module hint from logs if not provided on the alert.
        module_hint = state.get("module_hint", "")
        if not module_hint:
            for _ts, line in raw_entries[:50]:
                m = _MODULE_RE.search(line)
                if m:
                    module_hint = m.group(1)
                    break

        # Expand blast radius with module-specific backing deps.
        if module_hint:
            for dep in self.dep_map.module_dependencies(module_hint):
                if dep not in blast_radius:
                    blast_radius.append(dep)

        return {
            **state,
            "dependencies": dependencies,
            "blast_radius": blast_radius,
            "prom_data": prom_data,
            "loki_logs": messages,
            "log_source": log_source,
            "module_hint": module_hint,
        }

    # ---- rag_lookup ----------------------------------------------------
    def rag_lookup(self, state: DiagnosticState) -> DiagnosticState:
        log_excerpt = " ".join(state.get("loki_logs", [])[:3])
        query = (
            f"{state.get('alert_type', '')} {state.get('service', '')} "
            f"{state.get('module_hint', '')} {log_excerpt}"
        ).strip()
        context = self.rag.query(query)
        return {**state, "rag_context": context}

    # ---- correlate -----------------------------------------------------
    def correlate(self, state: DiagnosticState) -> DiagnosticState:
        user_content = (
            f"Alert: {state.get('alert_type')} on {state.get('service')} "
            f"(severity: {state.get('severity')})\n"
            f"Suspected module: {state.get('module_hint') or 'unknown'}\n"
            f"Dependencies checked: {state.get('dependencies')}\n"
            f"Metrics snapshot: {json.dumps(state.get('prom_data', {}))}\n"
            f"Recent error/warn logs (sample): {state.get('loki_logs', [])[:10]}\n"
            f"Runbook / past-incident context: {state.get('rag_context') or 'none'}\n"
            f"Downstream services at risk: {state.get('blast_radius')}"
        )
        raw = ""
        token_usage = extract_token_usage(None)
        try:
            result = self.llm.invoke(
                [
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(content=user_content),
                ]
            )
            parsed = result.get("parsed") if isinstance(result, dict) else None
            raw_msg = result.get("raw") if isinstance(result, dict) else None
            raw = getattr(raw_msg, "content", "") or ""
            token_usage = extract_token_usage(raw_msg)
            if parsed is not None:
                hypotheses = parsed.model_dump()
            else:
                parsing_error = (
                    result.get("parsing_error") if isinstance(result, dict) else None
                )
                logger.warning("LLM structured output parse failed: %s", parsing_error)
                hypotheses = {
                    "error": "LLM did not return valid structured output",
                    "raw": raw,
                }
        except Exception as exc:  # noqa: BLE001 - never crash the graph on LLM errors
            logger.error("correlate failed: %s", exc)
            hypotheses = {"error": f"LLM call failed: {exc}"}

        rag_ctx = state.get("rag_context") or ""
        logger.info(
            "llm_exchange alert=%s service=%s tokens_in=%s tokens_out=%s "
            "tokens_total=%s rag_used=%s rag_chars=%d user_prompt_chars=%d",
            state.get("alert_type"),
            state.get("service"),
            token_usage.get("input_tokens"),
            token_usage.get("output_tokens"),
            token_usage.get("total_tokens"),
            bool(rag_ctx),
            len(rag_ctx),
            len(user_content),
        )
        return {
            **state,
            "hypotheses": hypotheses,
            "llm_raw": raw,
            "llm_system_prompt": SYSTEM_PROMPT,
            "llm_user_prompt": user_content,
            "llm_token_usage": token_usage,
        }

    # ---- report --------------------------------------------------------
    def report(self, state: DiagnosticState) -> DiagnosticState:
        rag_ctx = state.get("rag_context") or ""
        report = {
            "service": state.get("service"),
            "alert_type": state.get("alert_type"),
            "severity": state.get("severity"),
            "module": state.get("module_hint") or None,
            "dependencies_checked": state.get("dependencies", []),
            "blast_radius": state.get("blast_radius", []),
            "diagnosis": state.get("hypotheses", {}),
            "evidence": {
                "metrics": state.get("prom_data", {}),
                "error_log_sample": state.get("loki_logs", [])[:10],
                "log_source": state.get("log_source") or {},
                "rag_used": bool(rag_ctx),
            },
            # Full prompts + tokens for RAG effectiveness / cost (also in audit JSONL).
            "llm_exchange": {
                "system_prompt": state.get("llm_system_prompt") or SYSTEM_PROMPT,
                "user_prompt": state.get("llm_user_prompt") or "",
                "rag_context": rag_ctx,
                "rag_used": bool(rag_ctx),
                "token_usage": state.get("llm_token_usage")
                or extract_token_usage(None),
            },
        }
        return {**state, "report": report}
