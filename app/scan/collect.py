"""Collect evidence from a live stack.

Every source degrades independently: an unreachable Loki costs you the log
sections, not the scan. Only Prometheus is treated as required, because without
it there is nothing to correlate against.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from ..clients.alertmanager import AlertmanagerClient
from ..clients.loki import LokiClient
from ..clients.prometheus import PrometheusClient
from . import scrub
from .models import (
    AlertmanagerEvidence,
    AlertRule,
    LogSample,
    LokiEvidence,
    PrometheusEvidence,
    ScanEvidence,
    ScrapeTarget,
)

logger = logging.getLogger(__name__)

# Labels worth enumerating as possible service identifiers. Ordered by how
# commonly they carry the app name in the stacks this agent targets.
SERVICE_LABEL_CANDIDATES = (
    "service",
    "job",
    "app",
    "application",
    "container",
    "container_name",
    "namespace",
    "instance",
)

# Cap on metric names carried in the bundle; a busy stack exposes thousands and
# the report only ever shows a sample.
_METRIC_NAME_CAP = 400

# LogQL line filters: |~ "regex" and |= "substring", single or double quoted.
_LINE_FILTER_RE = re.compile(r"\|[~=]\s*(?P<quote>[\"'`])(?P<body>.*?)(?<!\\)(?P=quote)")
# Stream selector label matchers, e.g. {service="platform-service"}.
_SELECTOR_SERVICE_RE = re.compile(
    r"(?:service|app|job)\s*(?:=|=~)\s*[\"'](?P<value>[^\"']+)[\"']"
)
# Level-ish field names seen in structured logs, in preference order.
_LEVEL_FIELDS = ("level", "severity", "loglevel", "log_level", "levelname")


@dataclass(frozen=True)
class ScanOptions:
    """Knobs for one scan. Defaults are deliberately modest."""

    prometheus_url: str
    loki_url: str = ""
    alertmanager_url: str = ""
    timeout: float = 10.0
    lookback_minutes: int = 60
    sample_lines: int = 300
    max_services: int = 12
    include_samples: bool = True
    # Keep scrubbed sample lines in the bundle. Off by default: the census and
    # the derived fields are what later phases need, not the prose.
    keep_lines: bool = False
    workspace: str = ""


def collect_evidence(options: ScanOptions) -> ScanEvidence:
    """Probe every configured source and return one evidence bundle."""
    prometheus = _collect_prometheus(options)
    loki = _collect_loki(options, prometheus)
    alertmanager = _collect_alertmanager(options)

    from .analyze import analyze

    evidence = ScanEvidence(
        generated_at=datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        agent_version=_agent_version(),
        workspace=options.workspace,
        prometheus=prometheus,
        loki=loki,
        alertmanager=alertmanager,
    )
    return ScanEvidence(
        generated_at=evidence.generated_at,
        agent_version=evidence.agent_version,
        workspace=evidence.workspace,
        prometheus=prometheus,
        loki=loki,
        alertmanager=alertmanager,
        findings=analyze(evidence, options),
    )


def _agent_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("diagnostic-agent")
    except PackageNotFoundError:
        return "unknown"


# -- Prometheus --------------------------------------------------------------
def _collect_prometheus(options: ScanOptions) -> PrometheusEvidence:
    client = PrometheusClient(options.prometheus_url, timeout=options.timeout)
    notes: list[str] = []

    metric_names = client.metric_names()
    if not metric_names:
        return PrometheusEvidence(
            reachable=False,
            url=options.prometheus_url,
            notes=("no metric names returned; treat every other section as unverified",),
        )

    build = client.build_info()
    label_names = client.labels()

    label_values: dict[str, tuple[str, ...]] = {}
    for label in SERVICE_LABEL_CANDIDATES:
        if label_names and label not in label_names:
            continue
        values = client.label_values(label)
        if values:
            label_values[label] = tuple(sorted(values))

    targets = _reduce_targets(client.targets())
    if not targets:
        notes.append("no active scrape targets reported")

    rules = _prometheus_rules(client.rules())
    if not rules:
        notes.append("no alerting rules defined in Prometheus")

    return PrometheusEvidence(
        reachable=True,
        version=str(build.get("version") or ""),
        url=options.prometheus_url,
        metric_count=len(metric_names),
        metric_names=tuple(sorted(metric_names)[:_METRIC_NAME_CAP]),
        label_names=tuple(sorted(label_names)),
        label_values=label_values,
        targets=targets,
        rules=rules,
        notes=tuple(notes),
    )


def _reduce_targets(raw: list[dict]) -> tuple[ScrapeTarget, ...]:
    out: list[ScrapeTarget] = []
    for target in raw:
        labels = target.get("labels") if isinstance(target.get("labels"), dict) else {}
        out.append(
            ScrapeTarget(
                job=str(labels.get("job") or ""),
                instance=str(labels.get("instance") or ""),
                health=str(target.get("health") or "unknown"),
                service=str(labels.get("service") or ""),
            )
        )
    out.sort(key=lambda t: (t.job, t.instance))
    return tuple(out)


def _prometheus_rules(groups: list[dict]) -> tuple[AlertRule, ...]:
    out: list[AlertRule] = []
    for group in groups:
        for rule in group.get("rules") or []:
            if not isinstance(rule, dict) or rule.get("type") != "alerting":
                continue
            out.append(_build_rule(rule, source="prometheus", name_key="name"))
    out.sort(key=lambda r: r.name)
    return tuple(out)


def _build_rule(rule: dict, *, source: str, name_key: str) -> AlertRule:
    labels = rule.get("labels") if isinstance(rule.get("labels"), dict) else {}
    annotations = (
        rule.get("annotations") if isinstance(rule.get("annotations"), dict) else {}
    )
    expr = str(rule.get("query") or rule.get("expr") or "")
    runbook = str(
        annotations.get("runbook") or annotations.get("runbook_url") or ""
    ).strip()
    duration = rule.get("duration")
    if duration is None:
        duration = rule.get("for") or ""
    return AlertRule(
        name=str(rule.get(name_key) or rule.get("alert") or "").strip(),
        source=source,
        severity=str(labels.get("severity") or "").strip(),
        expr=scrub.scrub_text(expr),
        duration=str(duration),
        runbook=runbook,
        line_filters=_extract_line_filters(expr),
        services=_extract_selector_services(expr, labels),
    )


def _extract_line_filters(expr: str) -> tuple[str, ...]:
    """Pull ``|~``/``|=`` filters out of a LogQL expression.

    A log-based alert already carries the regex that decides which lines matter,
    so this is extraction rather than inference.
    """
    found = [m.group("body") for m in _LINE_FILTER_RE.finditer(expr or "")]
    seen: dict[str, None] = {}
    for item in found:
        if item.strip():
            seen.setdefault(item, None)
    return tuple(seen.keys())


def _extract_selector_services(expr: str, labels: dict) -> tuple[str, ...]:
    values: dict[str, None] = {}
    labelled = str(labels.get("service") or "").strip()
    if labelled:
        values.setdefault(labelled, None)
    for match in _SELECTOR_SERVICE_RE.finditer(expr or ""):
        value = match.group("value").strip()
        # Regex matchers can be alternations; keep the parts that look like names.
        for part in re.split(r"[|]", value):
            cleaned = part.strip().strip(".*")
            if cleaned and not any(ch in cleaned for ch in "()[]\\^$"):
                values.setdefault(cleaned, None)
    return tuple(values.keys())


# -- Loki --------------------------------------------------------------------
def _collect_loki(
    options: ScanOptions, prometheus: PrometheusEvidence
) -> LokiEvidence:
    if not options.loki_url:
        return LokiEvidence(notes=("no Loki URL configured",))

    client = LokiClient(options.loki_url, timeout=options.timeout)
    notes: list[str] = []
    label_names = client.labels()
    if not label_names:
        return LokiEvidence(
            reachable=False,
            url=options.loki_url,
            notes=("no labels returned; Loki unreachable or empty",),
        )

    label_values: dict[str, tuple[str, ...]] = {}
    for label in SERVICE_LABEL_CANDIDATES:
        if label not in label_names:
            continue
        values = client.label_values(label)
        if values:
            label_values[label] = tuple(sorted(values))

    service_label = _pick_service_label(label_values, prometheus)
    if not service_label:
        notes.append(
            "could not identify a service-ish label; set logs_profile.service_label "
            "by hand"
        )

    rules = _loki_rules(client.rules())
    if not rules:
        notes.append("no Loki ruler rules (ruler may be disabled)")

    samples: tuple[LogSample, ...] = ()
    secrets: tuple[scrub.SecretHit, ...] = ()
    level_field = ""
    hints: dict[str, tuple[str, ...]] = {}
    if options.include_samples and service_label:
        samples, secrets, level_field = _sample_streams(
            client, options, service_label, label_values.get(service_label, ())
        )
        if not samples:
            notes.append("no log lines in the sample window")
        hints = _log_service_hints(
            client,
            options,
            service_label,
            stream_values=label_values.get(service_label, ()),
            prometheus=prometheus,
        )
    elif not options.include_samples:
        notes.append("log sampling skipped (--no-samples)")

    return LokiEvidence(
        reachable=True,
        url=options.loki_url,
        label_names=tuple(sorted(label_names)),
        label_values=label_values,
        service_label=service_label,
        level_field=level_field,
        samples=samples,
        rules=rules,
        secrets=secrets,
        log_service_hints=hints,
        notes=tuple(notes),
    )


def _log_service_hints(
    client: LokiClient,
    options: ScanOptions,
    service_label: str,
    *,
    stream_values: tuple[str, ...],
    prometheus: PrometheusEvidence,
) -> dict[str, tuple[str, ...]]:
    """Find which stream carries the errors of a service that has no stream.

    A managed database, an external API or a logical alert target does not ship
    logs under its own name — its failures surface in the log stream of whatever
    talks to it. That redirect is the piece of ``service_map.yaml`` operators are
    least likely to get right by hand, and it is directly observable: search for
    the dependency's name across all streams and see which stream answers.
    """
    known = {value.lower() for value in stream_values}
    missing = [
        name
        for name in _prometheus_service_values(prometheus)
        if name.lower() not in known
    ][: options.max_services]
    if not missing:
        return {}

    hints: dict[str, tuple[str, ...]] = {}
    for name in missing:
        pattern = re.escape(name)
        logql = f'{{{service_label}=~".+"}} |~ "(?i){pattern}"'
        counts: dict[str, int] = {}
        for labels, lines in client.query_range_streams(
            logql, lookback_minutes=options.lookback_minutes, limit=50
        ):
            value = labels.get(service_label, "")
            if value and value.lower() != name.lower():
                counts[value] = counts.get(value, 0) + len(lines)
        if counts:
            ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            hints[name] = tuple(value for value, _count in ranked[:3])
    return hints


def _prometheus_service_values(prometheus: PrometheusEvidence) -> tuple[str, ...]:
    """Service-ish names Prometheus knows, best label first."""
    for label in ("service", "job", "app", "application"):
        values = prometheus.label_values.get(label)
        if values:
            return values
    return ()


def _pick_service_label(
    label_values: dict[str, tuple[str, ...]], prometheus: PrometheusEvidence
) -> str:
    """Choose the Loki label whose values look most like service names.

    Overlap with Prometheus decides it: a label naming the same things both
    systems know is what ``logs_profile.service_label`` has to be. With no
    overlap, fall back to candidate order.
    """
    prom_values: set[str] = set()
    for values in prometheus.label_values.values():
        prom_values.update(values)

    best_label = ""
    best_overlap = 0
    for label in SERVICE_LABEL_CANDIDATES:
        values = label_values.get(label)
        if not values:
            continue
        overlap = len(set(values) & prom_values)
        if overlap > best_overlap:
            best_label, best_overlap = label, overlap
    if best_label:
        return best_label
    for label in SERVICE_LABEL_CANDIDATES:
        if label_values.get(label):
            return label
    return ""


def _sample_streams(
    client: LokiClient,
    options: ScanOptions,
    service_label: str,
    values: tuple[str, ...],
) -> tuple[tuple[LogSample, ...], tuple[scrub.SecretHit, ...], str]:
    """Sample lines per stream value, scrub them, and derive log-shape facts."""
    targets = list(values)[: options.max_services]
    if not targets:
        return (), (), ""

    per_stream = max(20, options.sample_lines // len(targets))
    samples: list[LogSample] = []
    raw_lines: list[str] = []
    level_fields: dict[str, int] = {}

    for value in targets:
        selector = f'{{{service_label}="{value}"}}'
        entries = client.query_range(
            selector,
            lookback_minutes=options.lookback_minutes,
            limit=per_stream,
        )
        if not entries:
            continue
        lines = [line for _ts, line in entries]
        raw_lines.extend(lines)

        json_lines = 0
        levels: dict[str, None] = {}
        loggers: dict[str, None] = {}
        for line in lines:
            doc = _parse_json(line)
            if doc is None:
                continue
            json_lines += 1
            for candidate in _LEVEL_FIELDS:
                if candidate in doc:
                    level_fields[candidate] = level_fields.get(candidate, 0) + 1
                    level = str(doc.get(candidate) or "").strip()
                    if level:
                        levels.setdefault(level.upper(), None)
                    break
            name = doc.get("logger_name") or doc.get("logger") or doc.get("loggerName")
            if name:
                loggers.setdefault(str(name), None)

        scrubbed = scrub.scrub_lines(lines) if options.keep_lines else []
        samples.append(
            LogSample(
                stream_value=value,
                line_count=len(lines),
                json_lines=json_lines,
                lines=tuple(scrubbed[:10]),
                level_values=tuple(sorted(levels.keys())),
                logger_names=tuple(list(loggers.keys())[:20]),
            )
        )

    level_field = ""
    if level_fields:
        level_field = max(level_fields.items(), key=lambda kv: kv[1])[0]
    return tuple(samples), tuple(scrub.census(raw_lines)), level_field


def _parse_json(line: str) -> dict | None:
    try:
        doc = json.loads(line)
    except (ValueError, TypeError):
        return None
    return doc if isinstance(doc, dict) else None


def _loki_rules(namespaces: dict[str, list[dict]]) -> tuple[AlertRule, ...]:
    out: list[AlertRule] = []
    for groups in namespaces.values():
        for group in groups:
            for rule in group.get("rules") or []:
                if not isinstance(rule, dict) or not rule.get("alert"):
                    continue
                out.append(_build_rule(rule, source="loki", name_key="alert"))
    out.sort(key=lambda r: r.name)
    return tuple(out)


# -- Alertmanager ------------------------------------------------------------
def _collect_alertmanager(options: ScanOptions) -> AlertmanagerEvidence:
    if not options.alertmanager_url:
        return AlertmanagerEvidence(notes=("no Alertmanager URL configured",))

    client = AlertmanagerClient(options.alertmanager_url, timeout=options.timeout)
    version = client.version()
    receivers = client.receivers()
    if not version and not receivers:
        return AlertmanagerEvidence(
            reachable=False,
            url=options.alertmanager_url,
            notes=("no status or receivers returned",),
        )
    return AlertmanagerEvidence(
        reachable=True,
        url=options.alertmanager_url,
        version=version,
        receivers=tuple(receivers),
        firing=client.firing_alertnames(),
        notes=(),
    )
