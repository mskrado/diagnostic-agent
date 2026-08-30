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


class BundleError(ValueError):
    """A bundle cannot be read as evidence (wrong shape or newer schema)."""


def _strs(data: dict, key: str) -> tuple[str, ...]:
    value = data.get(key)
    return tuple(str(v) for v in value) if isinstance(value, list) else ()


def _str_map(data: dict, key: str) -> dict[str, tuple[str, ...]]:
    value = data.get(key)
    if not isinstance(value, dict):
        return {}
    return {
        str(k): tuple(str(item) for item in v)
        for k, v in value.items()
        if isinstance(v, list)
    }


def _dicts(data: dict, key: str) -> tuple[dict, ...]:
    value = data.get(key)
    return tuple(v for v in value if isinstance(v, dict)) if isinstance(value, list) else ()


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

    @classmethod
    def from_dict(cls, data: dict) -> "AlertRule":
        return cls(
            name=str(data.get("name") or ""),
            source=str(data.get("source") or ""),
            severity=str(data.get("severity") or ""),
            expr=str(data.get("expr") or ""),
            duration=str(data.get("duration") or ""),
            runbook=str(data.get("runbook") or ""),
            line_filters=_strs(data, "line_filters"),
            services=_strs(data, "services"),
        )


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

    @classmethod
    def from_dict(cls, data: dict) -> "ScrapeTarget":
        return cls(
            job=str(data.get("job") or ""),
            instance=str(data.get("instance") or ""),
            health=str(data.get("health") or "unknown"),
            service=str(data.get("service") or ""),
        )


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

    @classmethod
    def from_dict(cls, data: dict) -> "PrometheusEvidence":
        return cls(
            reachable=bool(data.get("reachable")),
            version=str(data.get("version") or ""),
            url=str(data.get("url") or ""),
            metric_count=int(data.get("metric_count") or 0),
            metric_names=_strs(data, "metric_names"),
            label_names=_strs(data, "label_names"),
            label_values=_str_map(data, "label_values"),
            targets=tuple(
                ScrapeTarget.from_dict(t) for t in _dicts(data, "targets")
            ),
            rules=tuple(AlertRule.from_dict(r) for r in _dicts(data, "rules")),
            notes=_strs(data, "notes"),
        )


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

    @classmethod
    def from_dict(cls, data: dict) -> "LogSample":
        return cls(
            stream_value=str(data.get("stream_value") or ""),
            line_count=int(data.get("line_count") or 0),
            json_lines=int(data.get("json_lines") or 0),
            lines=_strs(data, "lines"),
            level_values=_strs(data, "level_values"),
            logger_names=_strs(data, "logger_names"),
        )


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

    @classmethod
    def from_dict(cls, data: dict) -> "LokiEvidence":
        return cls(
            reachable=bool(data.get("reachable")),
            url=str(data.get("url") or ""),
            label_names=_strs(data, "label_names"),
            label_values=_str_map(data, "label_values"),
            service_label=str(data.get("service_label") or ""),
            level_field=str(data.get("level_field") or ""),
            samples=tuple(LogSample.from_dict(s) for s in _dicts(data, "samples")),
            rules=tuple(AlertRule.from_dict(r) for r in _dicts(data, "rules")),
            secrets=tuple(
                SecretHit(
                    name=str(s.get("name") or ""),
                    description=str(s.get("description") or ""),
                    lines=int(s.get("lines") or 0),
                    matches=int(s.get("matches") or 0),
                )
                for s in _dicts(data, "secrets")
            ),
            log_service_hints=_str_map(data, "log_service_hints"),
            notes=_strs(data, "notes"),
        )


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

    @classmethod
    def from_dict(cls, data: dict) -> "AlertmanagerEvidence":
        firing = data.get("firing")
        return cls(
            reachable=bool(data.get("reachable")),
            url=str(data.get("url") or ""),
            version=str(data.get("version") or ""),
            receivers=_strs(data, "receivers"),
            firing=(
                {str(k): int(v) for k, v in firing.items()}
                if isinstance(firing, dict)
                else {}
            ),
            notes=_strs(data, "notes"),
        )


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

    @classmethod
    def from_dict(cls, data: dict) -> "ServiceCandidate":
        return cls(
            name=str(data.get("name") or ""),
            has_metrics=bool(data.get("has_metrics")),
            has_logs=bool(data.get("has_logs")),
            kind_hints=_strs(data, "kind_hints"),
            log_services_hint=_strs(data, "log_services_hint"),
        )


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

    @classmethod
    def from_dict(cls, data: dict) -> "NamingMarker":
        return cls(
            metric=str(data.get("metric") or ""),
            present=bool(data.get("present")),
            means=str(data.get("means") or ""),
        )


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

    @classmethod
    def from_dict(cls, data: dict) -> "Findings":
        return cls(
            services=tuple(
                ServiceCandidate.from_dict(s) for s in _dicts(data, "services")
            ),
            naming_markers=tuple(
                NamingMarker.from_dict(m) for m in _dicts(data, "naming_markers")
            ),
            uncovered_alerts=_strs(data, "uncovered_alerts"),
            covered_alerts=_strs(data, "covered_alerts"),
            notes=_strs(data, "notes"),
        )


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

    @classmethod
    def from_dict(cls, data: dict) -> "ScanEvidence":
        """Rebuild evidence from a bundle written by an earlier scan."""
        if not isinstance(data, dict):
            raise BundleError("bundle is not a JSON object")
        schema = data.get("schema")
        if not isinstance(schema, int):
            raise BundleError("bundle has no integer 'schema' field")
        if schema > SCHEMA_VERSION:
            raise BundleError(
                f"bundle schema {schema} is newer than this agent supports "
                f"({SCHEMA_VERSION}); re-run diag scan"
            )
        return cls(
            generated_at=str(data.get("generated_at") or ""),
            agent_version=str(data.get("agent_version") or "unknown"),
            schema=schema,
            workspace=str(data.get("workspace") or ""),
            prometheus=PrometheusEvidence.from_dict(data.get("prometheus") or {}),
            loki=LokiEvidence.from_dict(data.get("loki") or {}),
            alertmanager=AlertmanagerEvidence.from_dict(
                data.get("alertmanager") or {}
            ),
            findings=Findings.from_dict(data.get("findings") or {}),
        )

    def all_rules(self) -> tuple[AlertRule, ...]:
        return self.prometheus.rules + self.loki.rules
