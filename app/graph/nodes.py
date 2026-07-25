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
from ..llm import content_to_text, invoke_structured_diagnosis
from ..llm_usage import extract_token_usage
from ..profile import get_profile
from ..rag.store import RagStore
from .prompts import build_system_prompt
from .state import DiagnosticState

logger = logging.getLogger(__name__)


def _module_regex() -> re.Pattern[str] | None:
    raw = get_profile().logs.module_regex
    if not raw:
        return None
    try:
        return re.compile(raw)
    except re.error:
        return None


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

        metrics = get_profile().metrics
        prom_data: dict = {}
        for svc in [service] + dependencies:
            snapshot: dict = {}
            kind = self.dep_map.kind(svc)
            if kind in metrics.service_kinds:
                for metric_name in metrics.service_metrics:
                    try:
                        query = metrics.render(metric_name, service=svc, window=window)
                    except Exception:  # noqa: BLE001
                        query = None
                    if not query:
                        continue
                    # Keep stable snapshot keys used by email/eval.
                    key = "up" if metric_name == "service_up" else metric_name
                    if metric_name == "jvm_heap_used_ratio":
                        key = "heap_used_ratio"
                    snapshot[key] = self.prom.instant(query)
            probe_q = promql.dependency_probe(kind, service, window)
            if probe_q is not None:
                snapshot[f"{kind}_probe"] = self.prom.instant(probe_q)
            prom_data[svc] = {k: v for k, v in snapshot.items() if v is not None}

        # Always-collect metrics on the alerted service (e.g. db_pool_pending).
        prom_data.setdefault(service, {})
        for metric_name in metrics.always_collect:
            try:
                query = metrics.render(metric_name, service=service, window=window)
            except Exception:  # noqa: BLE001
                query = None
            if not query:
                continue
            value = self.prom.instant(query)
            if value is not None:
                prom_data[service][metric_name] = value

        # Logs for the alert target. Logical labels (security, postgres, …)
        # map to real Loki streams via service_map; alert-specific line filters
        # mirror the Loki ruler so the email sample matches what fired.
        from ..log_queries import build_retrieve_logql

        logql, log_meta = build_retrieve_logql(
            service=service,
            alert_type=state.get("alert_type"),
            log_services=self.dep_map.log_services(service),
            log_selector=self.dep_map.log_selector(service),
        )
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
            "level": log_meta.get("level") or "(any)",
            "service": service,
            "log_services": log_meta.get("log_services") or [service],
        }
        if log_meta.get("line_filter"):
            log_source["line_filter"] = log_meta["line_filter"]

        # Refine module hint from logs if not provided on the alert.
        module_hint = state.get("module_hint", "")
        module_re = _module_regex()
        if not module_hint and module_re is not None:
            for _ts, line in raw_entries[:50]:
                m = module_re.search(line)
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
        # Retrieve per distinct error family across the *full* log sample so
        # mixed incidents pull redis/jvm/postgres runbooks together — not only
        # whatever family appears in logs[:3].
        from ..rag.queries import build_rag_queries

        logs = list(state.get("loki_logs") or [])
        queries = build_rag_queries(
            alert_type=state.get("alert_type", "") or "",
            service=state.get("service", "") or "",
            module_hint=state.get("module_hint", "") or "",
            log_lines=logs,
        )
        context = self.rag.query_many(queries)
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
        system_prompt = build_system_prompt()
        try:
            result = invoke_structured_diagnosis(
                self.llm,
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_content),
                ],
            )
            parsed = result.get("parsed") if isinstance(result, dict) else None
            raw_msg = result.get("raw") if isinstance(result, dict) else None
            raw = content_to_text(getattr(raw_msg, "content", ""))
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
            "llm_system_prompt": system_prompt,
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
            # Chat/embed ids for email + audit (also mirrored on llm_exchange).
            "models": settings.model_snapshot(),
            # Full prompts + tokens for RAG effectiveness / cost (also in audit JSONL).
            "llm_exchange": {
                "system_prompt": state.get("llm_system_prompt")
                or build_system_prompt(),
                "user_prompt": state.get("llm_user_prompt") or "",
                "rag_context": rag_ctx,
                "rag_used": bool(rag_ctx),
                "token_usage": state.get("llm_token_usage")
                or extract_token_usage(None),
                **settings.model_snapshot(),
            },
        }
        return {**state, "report": report}
