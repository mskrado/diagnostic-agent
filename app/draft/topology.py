"""Draft `service_map.yaml` from evidence.

Nodes only exist if something observable backs them. Edges are proposed from the
most reliable signal available and each one is checked, because a wrong edge
silently widens or narrows the blast radius the agent reports.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..scan.models import ScanEvidence
from . import render
from .models import REJECTED, UNVERIFIED, VERIFIED, Candidate, DraftedFile
from .verify import Oracle

logger = logging.getLogger(__name__)

# Client-side metric families that prove an application talks to a dependency
# kind. `{label}`/`{service}` are filled per service.
_EDGE_PROBES: dict[str, tuple[str, ...]] = {
    "database": (
        'hikaricp_connections_active{{{label}="{service}"}}',
        'spring_data_repository_invocations_seconds_count{{{label}="{service}"}}',
    ),
    "redis": ('lettuce_command_completion_seconds_count{{{label}="{service}"}}',),
    "search": (
        'elasticsearch_rest_client_requests_seconds_count{{{label}="{service}"}}',
    ),
    "queue": ('kafka_consumer_fetch_manager_records_consumed_total{{{label}="{service}"}}',),
}

# Tempo's metrics generator publishes the call graph directly.
_SERVICE_GRAPH = "traces_service_graph_request_total"

# Kinds that never call anything: an edge out of a datastore would be wrong.
_LEAF_KINDS = frozenset(
    {"database", "redis", "search", "queue", "object-store", "external-api"}
)

_APP_KINDS = ("gateway", "monolith", "http")


@dataclass
class Node:
    name: str
    kind: str
    description: str
    downstream: list[str] = field(default_factory=list)
    upstream: list[str] = field(default_factory=list)
    log_services: list[str] = field(default_factory=list)
    # Edges proposed but not confirmed, kept for the commented-out block.
    withheld_downstream: list[tuple[str, str]] = field(default_factory=list)


def build_nodes(
    evidence: ScanEvidence, oracle: Oracle
) -> tuple[tuple[Node, ...], tuple[Candidate, ...]]:
    """Build the node set and the candidate record behind it."""
    candidates: list[Candidate] = []
    nodes: dict[str, Node] = {}

    for service in evidence.findings.services:
        kind = _kind_for(service)
        node = Node(
            name=service.name,
            kind=kind,
            description=_describe(service, kind),
            log_services=list(service.log_services_hint),
        )
        nodes[service.name] = node
        candidates.append(
            Candidate(
                key=f"services.{service.name}",
                value={"kind": kind},
                why=_node_evidence(service),
                verdict=VERIFIED,
                detail=(
                    "metrics and logs" if service.has_metrics and service.has_logs
                    else "metrics only" if service.has_metrics
                    else "logs only"
                ),
            )
        )
        if service.log_services_hint:
            candidates.append(
                Candidate(
                    key=f"services.{service.name}.log_services",
                    value=list(service.log_services_hint),
                    why=(
                        f"{service.name} has no Loki stream; its name appears in "
                        f"these streams"
                    ),
                    verdict=VERIFIED,
                    detail="discovered by searching every stream for the name",
                )
            )

    if not nodes:
        return (), tuple(candidates)

    edge_candidates = _graph_edges(evidence, oracle, nodes)
    if not edge_candidates:
        edge_candidates = _fingerprint_edges(evidence, oracle, nodes)
    candidates.extend(edge_candidates)

    for node in nodes.values():
        for target in node.downstream:
            if target in nodes and node.name not in nodes[target].upstream:
                nodes[target].upstream.append(node.name)

    return tuple(nodes[name] for name in sorted(nodes)), tuple(candidates)


def _kind_for(service) -> str:
    if service.kind_hints:
        return service.kind_hints[0]
    if service.has_metrics:
        return "http"
    return "unknown"


def _describe(service, kind: str) -> str:
    where = []
    if service.has_metrics:
        where.append("metrics")
    if service.has_logs:
        where.append("logs")
    return f"{kind}; observed in {' and '.join(where) or 'no source'}"


def _node_evidence(service) -> str:
    parts = []
    if service.has_metrics:
        parts.append("Prometheus service label")
    if service.has_logs:
        parts.append("Loki stream")
    return "present in " + " and ".join(parts)


def _graph_edges(
    evidence: ScanEvidence, oracle: Oracle, nodes: dict[str, Node]
) -> list[Candidate]:
    """Read edges straight off Tempo's service-graph metric, when present.

    This is the only signal that states the call graph rather than implying it,
    so it wins outright when the metrics generator is enabled.
    """
    if _SERVICE_GRAPH not in evidence.prometheus.metric_names:
        return []
    pairs = oracle.label_pairs(_SERVICE_GRAPH, "client", "server")
    out: list[Candidate] = []
    for client, server in pairs:
        if client not in nodes or server not in nodes or client == server:
            continue
        if server not in nodes[client].downstream:
            nodes[client].downstream.append(server)
        out.append(
            Candidate(
                key=f"services.{client}.downstream",
                value=server,
                why=f"{_SERVICE_GRAPH} reports {client} calling {server}",
                verdict=VERIFIED,
                detail="from the tracing service graph",
            )
        )
    return out


def _fingerprint_edges(
    evidence: ScanEvidence, oracle: Oracle, nodes: dict[str, Node]
) -> list[Candidate]:
    """Infer edges from client-side metric families, then confirm each by query.

    An application exposing `hikaricp_*` is talking to a database; which database
    is the one in the node set. Without a tracing service graph this is the best
    available signal, so it is proposed rather than asserted where ambiguous.
    """
    label = _metric_label(evidence)
    if not label:
        return []

    apps = [n for n in nodes.values() if n.kind in _APP_KINDS]
    out: list[Candidate] = []
    for app in apps:
        for kind, templates in _EDGE_PROBES.items():
            targets = [n.name for n in nodes.values() if n.kind == kind]
            if not targets:
                continue
            confirmed = False
            detail = "no client metrics for this dependency kind"
            for template in templates:
                query = template.format(label=label, service=app.name)
                ok, detail = oracle.promql(query)
                if ok:
                    confirmed = True
                    break
            for target in targets:
                if confirmed:
                    if target not in app.downstream:
                        app.downstream.append(target)
                    out.append(
                        Candidate(
                            key=f"services.{app.name}.downstream",
                            value=target,
                            why=f"{app.name} exposes {kind} client metrics",
                            verdict=VERIFIED,
                            detail=detail,
                        )
                    )
                else:
                    app.withheld_downstream.append((target, detail))
                    out.append(
                        Candidate(
                            key=f"services.{app.name}.downstream",
                            value=target,
                            why=f"{target} is a {kind}, but {app.name} shows no client metrics",
                            verdict=REJECTED,
                            detail=detail,
                        )
                    )

    out.extend(_gateway_edges(nodes))
    return out


def _gateway_edges(nodes: dict[str, Node]) -> list[Candidate]:
    """Gateway-to-application edges, which no metric proves on its own.

    A single gateway in front of a single application is the overwhelmingly
    common shape, but "common" is not evidence, so it is proposed commented out.
    """
    gateways = [n for n in nodes.values() if n.kind == "gateway"]
    apps = [n for n in nodes.values() if n.kind in ("monolith", "http")]
    if len(gateways) != 1 or not apps:
        return []
    gateway = gateways[0]
    out: list[Candidate] = []
    for app in apps:
        if app.name in gateway.downstream:
            continue
        gateway.withheld_downstream.append((app.name, "no signal proves this edge"))
        out.append(
            Candidate(
                key=f"services.{gateway.name}.downstream",
                value=app.name,
                why=f"{gateway.name} is the only gateway and {app.name} is an application",
                verdict=UNVERIFIED,
                detail=(
                    "request-level evidence would need tracing or per-route "
                    "metrics; confirm from your routing config"
                ),
            )
        )
    return out


def _metric_label(evidence: ScanEvidence) -> str:
    for label in ("service", "job", "app", "application"):
        if evidence.prometheus.label_values.get(label):
            return label
    return ""


def render_service_map(
    nodes: tuple[Node, ...], candidates: tuple[Candidate, ...], evidence: ScanEvidence
) -> DraftedFile:
    body: list[str] = ["services:"]
    for node in nodes:
        body.append(f"{render.INDENT}{node.name}:")
        body.extend(render.entry("kind", node.kind, indent=2))
        body.extend(render.entry("upstream", list(node.upstream), indent=2))
        body.extend(render.entry("downstream", list(node.downstream), indent=2))
        if node.withheld_downstream:
            # One comment block, not one commented `downstream:` per candidate:
            # uncommenting two of those would leave a duplicate key.
            pad = render.INDENT * 2
            body.append(f"{pad}# downstream candidates not written; add them to the")
            body.append(f"{pad}# list above if you know better than the metrics:")
            for target, reason in node.withheld_downstream:
                body.append(f"{pad}#   - {target}  ({reason})")
        if node.log_services:
            body.append(
                f"{render.INDENT * 2}# {node.name} has no Loki stream of its own"
            )
            body.extend(render.entry("log_services", node.log_services, indent=2))
        body.extend(render.entry("description", node.description, indent=2))
        body.append("")

    graph_used = _SERVICE_GRAPH in evidence.prometheus.metric_names
    evidence_lines = [
        f"{len(nodes)} node(s), each backed by a Prometheus label value or a Loki stream",
        (
            f"edges from {_SERVICE_GRAPH} (tracing service graph)"
            if graph_used
            else "edges inferred from client-side metric families and confirmed by query"
        ),
    ]
    hints = sum(1 for node in nodes if node.log_services)
    if hints:
        evidence_lines.append(
            f"{hints} log_services redirect(s) discovered by searching streams for the name"
        )

    header = render.header(
        "service_map.yaml",
        purpose="Services, dependency edges, and where each service's logs live.",
        usage=(
            "Blast radius comes from `downstream`; `kind` selects dependency "
            "probes; `log_services` redirects log retrieval."
        ),
        evidence=evidence_lines,
        configure=(
            "Add edges your stack has that metrics cannot show (queues, "
            "cron paths). Remove nodes you do not want diagnosed."
        ),
        has_withheld=any(not c.accepted for c in candidates),
    )
    return DraftedFile(
        path="service_map.yaml",
        content=render.document(header, body),
        candidates=candidates,
    )
