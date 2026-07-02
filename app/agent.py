"""Wires collaborators into a compiled graph and runs a single diagnosis.

Built once at startup (clients, dependency map, RAG store, LLM, graph) and
reused per alert -- the RAG store in particular is expensive to build, so it is
NOT rebuilt on every request.
"""
from __future__ import annotations

import logging

from .clients.grafana import GrafanaClient
from .clients.loki import LokiClient
from .clients.prometheus import PrometheusClient
from .config import settings
from .delivery.annotation import deliver_annotation
from .delivery.audit import write_audit_record
from .delivery.email import deliver_email
from .dependency_map import get_dependency_map
from .graph.build import build_diagnostic_graph
from .graph.nodes import DiagnosticNodes
from .llm import get_chat_model
from .rag.store import build_rag_store

logger = logging.getLogger(__name__)


class DiagnosticAgent:
    def __init__(self):
        self.prom = PrometheusClient(settings.prometheus_url)
        self.loki = LokiClient(settings.loki_url)
        self.grafana = GrafanaClient(settings.grafana_url, settings.grafana_token)
        self.dep_map = get_dependency_map(settings.service_map_path)
        self.rag = build_rag_store()
        self.llm = get_chat_model()
        self.nodes = DiagnosticNodes(
            self.prom, self.loki, self.grafana, self.dep_map, self.rag, self.llm
        )
        self.graph = build_diagnostic_graph(self.nodes)
        logger.info(
            "DiagnosticAgent ready (llm=%s, rag=%s, grafana=%s, email=%s)",
            settings.llm_provider,
            self.rag.available,
            self.grafana.enabled,
            settings.email_enabled,
        )

    def diagnose(self, alert: dict) -> dict:
        """Run the graph for one Alertmanager alert dict and deliver the report."""
        labels = alert.get("labels", {})
        initial = {
            "raw_labels": labels,
            "service": labels.get("service") or labels.get("job"),
            "alert_type": labels.get("alertname"),
            "severity": labels.get("severity"),
        }
        final = self.graph.invoke(initial)
        report = final.get("report", {})

        write_audit_record(report, final.get("llm_raw", ""))
        deliver_annotation(self.grafana, report)
        deliver_email(report, alert)
        return report
