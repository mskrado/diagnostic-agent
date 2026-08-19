"""Wires collaborators into a compiled graph and runs a single diagnosis.

Built once at startup (clients, dependency map, RAG store, LLM, graph) and
reused per alert -- the RAG store in particular is expensive to build, so it is
NOT rebuilt on every request.
"""
from __future__ import annotations

import logging

from .clients.grafana import GrafanaClient
from .clients.loki import LokiClient
from .clients.pagerduty import PagerDutyClient
from .clients.prometheus import PrometheusClient
from .config import settings
from .delivery.annotation import deliver_annotation
from .delivery.audit import write_audit_record
from .delivery.email import deliver_email
from .delivery.pagerduty import deliver_pagerduty
from .delivery.redact import active_rule_names
from .dependency_map import get_dependency_map
from .graph.build import build_diagnostic_graph
from .graph.nodes import DiagnosticNodes
from .llm import get_structured_diagnosis_llm
from .rag.store import build_rag_store

logger = logging.getLogger(__name__)


def _check_redaction() -> tuple[str, ...]:
    """Fail fast when the active profile resolves to no redaction rules.

    An empty/mis-mounted profile dir used to silently disable redaction, sending
    raw tenant identifiers into reports, audit records, and annotations. Set
    AGENT_REQUIRE_REDACTION=false to accept that risk deliberately.
    """
    from .profile import get_profile

    profile = get_profile()
    if profile.load_errors:
        raise RuntimeError(
            "Integration profile YAML failed to parse (refusing to start with "
            "silent preset fallback):\n  - "
            + "\n  - ".join(profile.load_errors)
        )

    names = active_rule_names()
    if names:
        return names
    message = (
        "Active integration profile resolved 0 redaction rules — reports would "
        "carry unredacted data. Check AGENT_PROFILE_DIR / redaction.yaml."
    )
    if settings.require_redaction:
        raise RuntimeError(f"{message} Set AGENT_REQUIRE_REDACTION=false to override.")
    logger.error("%s Continuing because AGENT_REQUIRE_REDACTION=false.", message)
    return names


class DiagnosticAgent:
    def __init__(self):
        self.redaction_rules = _check_redaction()
        self.prom = PrometheusClient(settings.prometheus_url)
        self.loki = LokiClient(settings.loki_url)
        self.grafana = GrafanaClient(settings.grafana_url, settings.grafana_token)
        self.pagerduty = PagerDutyClient(
            settings.pagerduty_api_url,
            settings.pagerduty_api_token,
            settings.pagerduty_service_id,
            settings.pagerduty_from_email,
            timeout=settings.pagerduty_timeout,
        )
        self.dep_map = get_dependency_map(settings.resolved_service_map_path())
        self.rag = build_rag_store()
        self.llm = get_structured_diagnosis_llm()
        self.nodes = DiagnosticNodes(
            self.prom, self.loki, self.grafana, self.dep_map, self.rag, self.llm
        )
        self.graph = build_diagnostic_graph(self.nodes)
        logger.info(
            "DiagnosticAgent ready (chat=%s/%s, rag=%s, grafana=%s, email=%s, pagerduty=%s, "
            "redaction=%d rules)",
            settings.chat_provider,
            settings.chat_model,
            self.rag.available,
            self.grafana.enabled,
            settings.email_enabled,
            settings.pagerduty_enabled,
            len(self.redaction_rules),
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
        pagerduty_result = deliver_pagerduty(self.pagerduty, report, alert)
        if pagerduty_result:
            report["pagerduty"] = pagerduty_result

        write_audit_record(report, final.get("llm_raw", ""))
        deliver_annotation(self.grafana, report)
        deliver_email(report, alert)
        return report
