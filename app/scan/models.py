"""Evidence bundle written by ``diag scan``.

Frozen dataclasses with explicit ``to_dict`` so the JSON shape is a deliberate,
reviewable contract rather than whatever the internals happen to look like. The
bundle is schema-versioned for the same reason ``agent.yaml`` is: a later phase
consumes it, possibly from an older scan.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .scrub import SecretHit

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class AlertRule:
    """One alerting rule, from either the Prometheus or the Loki ruler."""

    name: str
    source: str
    severity: str = ""
    expr: str = ""
    duration: str = ""
    runbook: str = ""
    # Line filters lifted out of a LogQL expression (``|~ "…"`` / ``|= "…"``).
    # A log-based alert already states the regex the agent should read, so there
    # is nothing to guess later.
    line_filters: tuple[str, ...] = ()
    services: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "source": self.source,
            "severity": self.severity,
            "expr": self.expr,
            "duration": self.duration,
            "runbook": self.runbook,
            "line_filters": list(self.line_filters),
            "services": list(self.services),
        }


@dataclass(frozen=True)
class ScrapeTarget:
    """A Prometheus scrape target, reduced to the fields a scan reports."""

    job: str
    instance: str
    health: str
    service: str = ""

    def to_dict(self) -> dict:
        return {
            "job": self.job,
            "instance": self.instance,
            "health": self.health,
            "service": self.service,
        }


@dataclass(frozen=True)
class PrometheusEvidence:
    reachable: bool = False
    version: str = ""
    url: str = ""
    metric_count: int = 0
    # Capped sample of metric names; the full list can run to thousands.
    metric_names: tuple[str, ...] = ()
    label_names: tuple[str, ...] = ()
    # Candidate service-identifying label -> its values.
    label_values: dict[str, tuple[str, ...]] = field(default_factory=dict)
    targets: tuple[ScrapeTarget, ...] = ()
    rules: tuple[AlertRule, ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "reachable": self.reachable,
            "version": self.version,
            "url": self.url,
            "metric_count": self.metric_count,
            "metric_names": list(self.metric_names),
            "label_names": list(self.label_names),
            "label_values": {k: list(v) for k, v in self.label_values.items()},
            "targets": [t.to_dict() for t in self.targets],
            "rules": [r.to_dict() for r in self.rules],
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class LogSample:
    """Scrubbed lines for one stream value, plus what they tell us."""

    stream_value: str
    line_count: int
    json_lines: int
    lines: tuple[str, ...] = ()
    level_values: tuple[str, ...] = ()
    logger_names: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "stream_value": self.stream_value,
            "line_count": self.line_count,
            "json_lines": self.json_lines,
            "lines": list(self.lines),
            "level_values": list(self.level_values),
            "logger_names": list(self.logger_names),
        }


@dataclass(frozen=True)
class LokiEvidence:
    reachable: bool = False
    url: str = ""
    label_names: tuple[str, ...] = ()
    label_values: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # The label whose values look most like service names (see analyze).
    service_label: str = ""
    level_field: str = ""
    samples: tuple[LogSample, ...] = ()
    rules: tuple[AlertRule, ...] = ()
    secrets: tuple[SecretHit, ...] = ()
    # Service with no stream of its own -> the streams that mention it. Becomes
    # the ``log_services`` redirect in service_map.yaml.
    log_service_hints: dict[str, tuple[str, ...]] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "reachable": self.reachable,
            "url": self.url,
            "label_names": list(self.label_names),
            "label_values": {k: list(v) for k, v in self.label_values.items()},
            "service_label": self.service_label,
            "level_field": self.level_field,
            "samples": [s.to_dict() for s in self.samples],
            "rules": [r.to_dict() for r in self.rules],
            "secrets": [s.to_dict() for s in self.secrets],
            "log_service_hints": {
                k: list(v) for k, v in self.log_service_hints.items()
            },
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class AlertmanagerEvidence:
    reachable: bool = False
    url: str = ""
    version: str = ""
    receivers: tuple[str, ...] = ()
    firing: dict[str, int] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "reachable": self.reachable,
            "url": self.url,
            "version": self.version,
            "receivers": list(self.receivers),
            "firing": dict(self.firing),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class ServiceCandidate:
    """A name that looks like a service, and the evidence behind it."""

    name: str
    has_metrics: bool = False
    has_logs: bool = False
    kind_hints: tuple[str, ...] = ()
    # When a dependency does not log under its own name, the stream that does
    # carry its errors — the ``log_services`` redirect, discovered rather than
    # guessed.
    log_services_hint: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "has_metrics": self.has_metrics,
            "has_logs": self.has_logs,
            "kind_hints": list(self.kind_hints),
            "log_services_hint": list(self.log_services_hint),
        }


@dataclass(frozen=True)
class NamingMarker:
    """Whether a metric that identifies a naming convention is present.

    Evidence only. Choosing a preset from these markers is a later phase.
    """

    metric: str
    present: bool
    means: str

    def to_dict(self) -> dict:
        return {"metric": self.metric, "present": self.present, "means": self.means}


@dataclass(frozen=True)
class Findings:
    """Cross-referenced conclusions drawn from the raw evidence."""

    services: tuple[ServiceCandidate, ...] = ()
    naming_markers: tuple[NamingMarker, ...] = ()
    # Alert names with no scenario in the current workspace.
    uncovered_alerts: tuple[str, ...] = ()
    covered_alerts: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "services": [s.to_dict() for s in self.services],
            "naming_markers": [m.to_dict() for m in self.naming_markers],
            "uncovered_alerts": list(self.uncovered_alerts),
            "covered_alerts": list(self.covered_alerts),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class ScanEvidence:
    generated_at: str
    agent_version: str
    schema: int = SCHEMA_VERSION
    workspace: str = ""
    prometheus: PrometheusEvidence = field(default_factory=PrometheusEvidence)
    loki: LokiEvidence = field(default_factory=LokiEvidence)
    alertmanager: AlertmanagerEvidence = field(default_factory=AlertmanagerEvidence)
    findings: Findings = field(default_factory=Findings)

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "generated_at": self.generated_at,
            "agent_version": self.agent_version,
            "workspace": self.workspace,
            "prometheus": self.prometheus.to_dict(),
            "loki": self.loki.to_dict(),
            "alertmanager": self.alertmanager.to_dict(),
            "findings": self.findings.to_dict(),
        }

    def all_rules(self) -> tuple[AlertRule, ...]:
        return self.prometheus.rules + self.loki.rules
