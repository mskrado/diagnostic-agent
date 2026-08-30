"""Cross-reference raw evidence into findings.

Pure functions over an already-collected bundle: no HTTP, no file writes. The
point is to answer the questions an operator actually has — what services do I
have, does my metric naming match a preset, which alerts have no runbook — while
stopping short of deciding anything. Deciding is generation, and generation is a
later phase.
"""
from __future__ import annotations

import logging

from .models import Findings, NamingMarker, ScanEvidence, ServiceCandidate

logger = logging.getLogger(__name__)

# Metrics that identify a naming convention or a dependency kind. Presence is
# evidence; picking a preset from it is Phase 2's job.
_NAMING_MARKERS: tuple[tuple[str, str], ...] = (
    ("http_requests_total", "community naming (generic-prometheus preset)"),
    (
        "http_server_requests_seconds_count",
        "Spring Boot Actuator / Micrometer (spring-micrometer preset)",
    ),
    ("http_server_duration_milliseconds_count", "OpenTelemetry HTTP semconv"),
    ("istio_requests_total", "Istio service mesh"),
    ("traefik_service_requests_total", "Traefik ingress"),
    ("nginx_http_requests_total", "nginx exporter"),
    ("hikaricp_connections_pending", "HikariCP JDBC pool (database dependency)"),
    ("lettuce_command_completion_seconds_count", "Lettuce Redis client"),
    ("redis_up", "Redis exporter"),
    ("pg_up", "Postgres exporter"),
    ("mysql_up", "MySQL exporter"),
    ("elasticsearch_cluster_health_status", "Elasticsearch exporter"),
    ("kafka_consumergroup_lag", "Kafka consumer lag"),
    ("jvm_memory_used_bytes", "JVM runtime metrics"),
    ("node_filesystem_avail_bytes", "node-exporter host metrics"),
    ("container_memory_usage_bytes", "cAdvisor container metrics"),
    ("traces_service_graph_request_total", "Tempo service graph (topology edges)"),
)

# Substrings in a service name that suggest a service_map `kind`. Hints only:
# names are conventions, and the operator owns the final answer.
_KIND_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("postgres", "postgre", "psql", "pgbouncer"), "database"),
    (("mysql", "mariadb"), "database"),
    (("redis", "valkey"), "redis"),
    (("elastic", "opensearch"), "search"),
    (("kafka", "rabbit", "sqs", "pubsub"), "queue"),
    (("minio", "s3", "blob"), "object-store"),
    (("gateway", "nginx", "traefik", "envoy", "ingress", "proxy"), "gateway"),
    (("mongo", "cassandra", "dynamo"), "database"),
    (("smtp", "mail", "sendgrid", "ses"), "external-api"),
    (("frontend", "web", "ui"), "frontend"),
)

# Infrastructure that is usually the observability stack itself rather than a
# service the agent should diagnose.
_INFRA_NAMES = frozenset(
    {
        "prometheus",
        "alertmanager",
        "loki",
        "grafana",
        "tempo",
        "promtail",
        "alloy",
        "node-exporter",
        "nodeexporter",
        "cadvisor",
        "blackbox-exporter",
        "pushgateway",
        "mailpit",
        "ollama",
        "diagnostic-agent",
    }
)


def analyze(evidence: ScanEvidence, options=None) -> Findings:
    """Build :class:`Findings` from collected evidence."""
    services = _service_candidates(evidence)
    markers = _naming_markers(evidence)
    covered, uncovered = _alert_coverage(evidence, options)
    notes = _notes(evidence, services)
    return Findings(
        services=services,
        naming_markers=markers,
        covered_alerts=covered,
        uncovered_alerts=uncovered,
        notes=notes,
    )


def _service_candidates(evidence: ScanEvidence) -> tuple[ServiceCandidate, ...]:
    """Names that look like services, with the evidence behind each."""
    metric_names: set[str] = set()
    for label in ("service", "job", "app", "application"):
        values = evidence.prometheus.label_values.get(label)
        if values:
            metric_names.update(values)
            break

    log_label = evidence.loki.service_label
    log_names = set(evidence.loki.label_values.get(log_label, ()) if log_label else ())

    hints = evidence.loki.log_service_hints
    candidates: list[ServiceCandidate] = []
    for name in sorted(metric_names | log_names):
        if _is_infra(name):
            continue
        candidates.append(
            ServiceCandidate(
                name=name,
                has_metrics=name in metric_names,
                has_logs=name in log_names,
                kind_hints=_kind_hints(name),
                log_services_hint=hints.get(name, ()),
            )
        )
    return tuple(candidates)


def _is_infra(name: str) -> bool:
    lowered = name.lower()
    if lowered in _INFRA_NAMES:
        return True
    # Prometheus `job` values often carry a port suffix, e.g. "loki:3100".
    return lowered.split(":")[0] in _INFRA_NAMES


def _kind_hints(name: str) -> tuple[str, ...]:
    lowered = name.lower()
    kinds: dict[str, None] = {}
    for needles, kind in _KIND_HINTS:
        if any(needle in lowered for needle in needles):
            kinds.setdefault(kind, None)
    return tuple(kinds.keys())


def _naming_markers(evidence: ScanEvidence) -> tuple[NamingMarker, ...]:
    """Which convention-identifying metrics exist.

    Only meaningful when the metric-name list was not truncated by the cap; a
    truncated list would report false absences, so fall back to substring
    matching over what we did collect.
    """
    present = set(evidence.prometheus.metric_names)
    markers: list[NamingMarker] = []
    for metric, means in _NAMING_MARKERS:
        markers.append(
            NamingMarker(metric=metric, present=metric in present, means=means)
        )
    return tuple(markers)


def _alert_coverage(evidence: ScanEvidence, options) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split live alert names by whether the workspace has a scenario for them."""
    alert_names = {rule.name for rule in evidence.all_rules() if rule.name}
    if not alert_names:
        return (), ()

    known = _workspace_alertnames(getattr(options, "workspace", "") if options else "")
    if known is None:
        # No workspace to compare against: report nothing rather than claiming
        # every alert is uncovered.
        return (), ()
    covered = sorted(name for name in alert_names if name in known)
    uncovered = sorted(name for name in alert_names if name not in known)
    return tuple(covered), tuple(uncovered)


def _workspace_alertnames(workspace: str) -> set[str] | None:
    """Alert names the workspace already has scenarios for, or None."""
    try:
        from ..tools.scenarios import load_scenarios
        from ..workspace import load as load_workspace

        ws = load_workspace(workspace or None)
        scenarios = load_scenarios(ws)
    except Exception as exc:  # noqa: BLE001 - a scan must survive any workspace state
        logger.debug("no workspace scenarios to compare against: %s", exc)
        return None

    names: set[str] = set()
    for scenario in scenarios:
        labels = scenario.get("labels") or {}
        name = str(labels.get("alertname") or "").strip()
        if name:
            names.add(name)
    return names


def _notes(
    evidence: ScanEvidence, services: tuple[ServiceCandidate, ...]
) -> tuple[str, ...]:
    notes: list[str] = []

    metrics_only = [s.name for s in services if s.has_metrics and not s.has_logs]
    logs_only = [s.name for s in services if s.has_logs and not s.has_metrics]
    if metrics_only:
        notes.append(
            f"{len(metrics_only)} service(s) have metrics but no log stream of their "
            f"own: {', '.join(metrics_only[:6])}"
        )
    if logs_only:
        notes.append(
            f"{len(logs_only)} service(s) log but expose no metrics under that name: "
            f"{', '.join(logs_only[:6])}"
        )
    if evidence.loki.reachable and not evidence.loki.service_label:
        notes.append(
            "no Loki label lines up with the Prometheus service names; log "
            "retrieval will need logs_profile.service_label set by hand"
        )
    log_rules = [r for r in evidence.loki.rules if r.line_filters]
    if log_rules:
        notes.append(
            f"{len(log_rules)} Loki rule(s) carry line filters that can seed "
            "logs_profile.alert_line_filters verbatim"
        )
    if evidence.prometheus.metric_count > len(evidence.prometheus.metric_names):
        notes.append(
            f"metric name list truncated to {len(evidence.prometheus.metric_names)} "
            f"of {evidence.prometheus.metric_count}"
        )
    return tuple(notes)
