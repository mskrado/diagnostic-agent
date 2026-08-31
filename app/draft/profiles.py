"""Draft `metrics_profile.yaml` and `logs_profile.yaml`.

Preset choice stops being a guess from a container name and becomes a
measurement: render each preset's metric suite against a real service and count
what comes back. The same query is the oracle for any override we then propose.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from ..profile.loader import list_presets, load_preset
from ..profile.models import MetricsProfile
from ..scan.models import ScanEvidence
from . import render
from .models import REJECTED, UNVERIFIED, VERIFIED, Candidate, DraftedFile, PresetScore
from .verify import Oracle, captures_group

logger = logging.getLogger(__name__)

# Metric labels that may carry the service name, best first.
_METRIC_LABELS = ("service", "job", "app", "application", "container")

# Levels worth retrieving when something is wrong. Anything else observed in the
# logs (INFO, DEBUG, TRACE) would drown the sample.
_SEVERE_LEVELS = ("FATAL", "CRITICAL", "SEVERE", "ERROR", "WARN", "WARNING")

# Per-kind probe candidates, tried in order. `{service}` binds to the *alerted*
# service at runtime, not the dependency, so these are metrics the application
# exposes about its client to that dependency (plus exporter-level fallbacks).
_KIND_PROBES: dict[str, tuple[str, ...]] = {
    "database": (
        'hikaricp_connections_pending{{service="{service}"}}',
        'pg_stat_database_numbackends',
        'pg_up',
        'mysql_up',
    ),
    "redis": (
        'lettuce_command_completion_seconds_count{{service="{service}"}}',
        'redis_connected_clients',
        'redis_up',
    ),
    "search": (
        'elasticsearch_cluster_health_status',
        'elasticsearch_indices_indexing_index_total',
    ),
    "queue": (
        'kafka_consumergroup_lag',
        'rabbitmq_queue_messages_ready',
    ),
    "external-api": (
        'http_client_requests_seconds_count{{service="{service}"}}',
    ),
}


@dataclass(frozen=True)
class ProbeTarget:
    """The service used to test queries, and the label that identifies it."""

    service: str
    metric_label: str


def probe_target(evidence: ScanEvidence) -> ProbeTarget | None:
    """Pick a real application service to test metric templates against.

    Preference order matters. A backing store answers none of the HTTP
    templates, and a gateway answers fewer of the application-level ones
    (connection pools, JVM), so an unadorned application service is the honest
    probe: templates that fail against it really are missing.
    """
    label = ""
    values: tuple[str, ...] = ()
    for candidate in _METRIC_LABELS:
        found = evidence.prometheus.label_values.get(candidate)
        if found:
            label, values = candidate, found
            break
    if not label:
        return None

    services = [s for s in evidence.findings.services if s.has_metrics]
    tiers = (
        [s.name for s in services if not s.kind_hints],
        [s.name for s in services if "gateway" in s.kind_hints],
        [s.name for s in services],
    )
    for tier in tiers:
        for name in tier:
            if name in values:
                return ProbeTarget(service=name, metric_label=label)
    return ProbeTarget(service=values[0], metric_label=label)


def _adapt_label(template: str, label: str) -> str:
    """Retarget a template's service matcher at the label this stack uses.

    Templates are written ``{{service="{service}"}}``; a stack that labels by
    ``job`` needs ``{{job="{service}"}}``. The ``{service}`` placeholder itself is
    untouched because it is not followed by ``="``.
    """
    if label == "service":
        return template
    return template.replace('service="', f'{label}="')


def _preset_metrics(name: str, label: str) -> MetricsProfile:
    raw = dict(load_preset(name).get("metrics") or {})
    templates = {
        key: _adapt_label(str(value), label)
        for key, value in (raw.get("templates") or {}).items()
    }
    raw["templates"] = templates
    return MetricsProfile.from_dict(raw)


def score_presets(
    evidence: ScanEvidence,
    oracle: Oracle,
    target: ProbeTarget,
    *,
    window: str = "5m",
) -> tuple[PresetScore, ...]:
    """Score every built-in preset by how much of its suite returns data."""
    markers = tuple(
        m.metric for m in evidence.findings.naming_markers if m.present
    )
    scores: list[PresetScore] = []
    for name in list_presets():
        profile = _preset_metrics(name, target.metric_label)
        checked = 0
        verified = 0
        for metric in profile.service_metrics:
            query = profile.render(metric, service=target.service, window=window)
            if not query:
                continue
            checked += 1
            ok, _detail = oracle.promql(query)
            verified += 1 if ok else 0
        scores.append(
            PresetScore(
                name=name,
                verified=verified,
                total=checked,
                probe_service=target.service,
                markers=markers,
            )
        )
    scores.sort(key=lambda s: (-s.ratio, -s.verified, s.name))
    return tuple(scores)


def choose_preset(scores: tuple[PresetScore, ...]) -> str:
    """The best-scoring preset, falling back to the base preset on a tie at zero."""
    if not scores or scores[0].verified == 0:
        return "generic-prometheus"
    return scores[0].name


def draft_metrics_profile(
    evidence: ScanEvidence,
    oracle: Oracle,
    *,
    preset: str,
    target: ProbeTarget | None,
    kinds: tuple[str, ...] = (),
    window: str = "5m",
    scores: tuple[PresetScore, ...] = (),
) -> DraftedFile:
    """`extends:` the measured preset, overriding only what verifies."""
    candidates: list[Candidate] = []
    evidence_lines = [
        f"preset chosen by query: {_score_summary(scores)}"
        if scores
        else "no preset scoring performed (no probe service)"
    ]
    body: list[str] = [f"extends: {preset}", ""]

    if target is None:
        candidates.append(
            Candidate(
                key="templates",
                value={},
                why="no service in Prometheus to test templates against",
                verdict=UNVERIFIED,
                detail="no probe service; preset templates left untouched",
            )
        )
        return _metrics_file(body, candidates, evidence_lines)

    evidence_lines.append(
        f"probe service {target.service!r} identified by label "
        f"{target.metric_label!r}"
    )

    profile = _preset_metrics(preset, target.metric_label)
    template_candidates: list[Candidate] = []
    if target.metric_label != "service":
        # The preset matches on service=; this stack does not. Every template
        # needs retargeting, so each one is proposed as an override.
        for metric in profile.service_metrics:
            raw = profile.templates.get(metric)
            if not raw:
                continue
            query = profile.render(metric, service=target.service, window=window)
            ok, detail = oracle.promql(query or "")
            template_candidates.append(
                Candidate(
                    key=metric,
                    value=raw,
                    why=(
                        f"preset matches on service=; this stack labels by "
                        f"{target.metric_label}="
                    ),
                    verdict=VERIFIED if ok else REJECTED,
                    detail=detail,
                )
            )
    else:
        # Preset templates already target the right label; only report the ones
        # that return nothing, so a reviewer knows what the agent cannot see.
        for metric in profile.service_metrics:
            raw = profile.templates.get(metric)
            if not raw:
                continue
            query = profile.render(metric, service=target.service, window=window)
            ok, detail = oracle.promql(query or "")
            if not ok:
                template_candidates.append(
                    Candidate(
                        key=metric,
                        value=raw,
                        why="preset template returned no data for the probe service",
                        verdict=REJECTED,
                        detail=detail,
                    )
                )

    template_lines = render.section("templates", tuple(template_candidates))
    if template_lines:
        body.extend([*template_lines, ""])
    candidates.extend(template_candidates)

    probe_candidates = _dependency_probes(
        oracle, kinds=kinds, target=target, window=window
    )
    probe_lines = render.section("dependency_probes", tuple(probe_candidates))
    if probe_lines:
        body.extend([*probe_lines, ""])
    candidates.extend(probe_candidates)

    return _metrics_file(body, candidates, evidence_lines)


def _score_summary(scores: tuple[PresetScore, ...]) -> str:
    return ", ".join(f"{s.name} {s.verified}/{s.total}" for s in scores)


def _dependency_probes(
    oracle: Oracle,
    *,
    kinds: tuple[str, ...],
    target: ProbeTarget,
    window: str,
) -> list[Candidate]:
    """First probe per kind that returns data; the kind's failures are dropped.

    Reporting four rejected probes per dependency would bury the file in noise,
    so only the last failure is kept as the record for that kind.
    """
    out: list[Candidate] = []
    for kind in kinds:
        options = _KIND_PROBES.get(kind)
        if not options:
            continue
        last_failure: Candidate | None = None
        for template in options:
            adapted = _adapt_label(template, target.metric_label)
            # Templates are stored with doubled braces; str.format collapses them.
            query = adapted.format(service=target.service, window=window)
            ok, detail = oracle.promql(query)
            candidate = Candidate(
                key=kind,
                value=adapted,
                why=f"probe for dependency kind {kind!r}",
                verdict=VERIFIED if ok else REJECTED,
                detail=detail,
            )
            if ok:
                out.append(candidate)
                break
            last_failure = candidate
        else:
            if last_failure is not None:
                out.append(last_failure)
    return out


def _metrics_file(
    body: list[str], candidates: list[Candidate], evidence_lines: list[str]
) -> DraftedFile:
    header = render.header(
        "metrics_profile.yaml",
        purpose="PromQL templates the agent renders per service and dependency kind.",
        usage=(
            "Merged onto the preset named by `extends:`; the correlate step "
            "renders these for the alerted service and its neighbours."
        ),
        evidence=evidence_lines,
        configure=(
            "Add or override templates under `templates:`. Placeholders are "
            "{service} and {window}; PromQL braces must be doubled."
        ),
        has_withheld=any(not c.accepted for c in candidates),
    )
    return DraftedFile(
        path="metrics_profile.yaml",
        content=render.document(header, body),
        candidates=tuple(candidates),
    )


# -- logs --------------------------------------------------------------------
def draft_logs_profile(evidence: ScanEvidence, oracle: Oracle) -> DraftedFile:
    """Every field here is measured from real streams or extracted from a rule."""
    loki = evidence.loki
    candidates: list[Candidate] = []
    evidence_lines: list[str] = []

    label = loki.service_label
    if label:
        ok, detail = oracle.logql(f'{{{label}=~".+"}}')
        candidates.append(
            Candidate(
                key="service_label",
                value=label,
                why="Loki label whose values overlap the Prometheus service names",
                verdict=VERIFIED if ok else REJECTED,
                detail=detail,
            )
        )
        evidence_lines.append(
            f"service_label {label!r} chosen from "
            f"{len(loki.label_values.get(label, ()))} stream value(s)"
        )
    else:
        candidates.append(
            Candidate(
                key="service_label",
                value="service",
                why="no Loki label overlapped the Prometheus service names",
                verdict=UNVERIFIED,
                detail="fell back to the preset default; confirm by hand",
            )
        )

    sample_lines = tuple(line for s in loki.samples for line in s.lines)
    total = sum(s.line_count for s in loki.samples)
    json_lines = sum(s.json_lines for s in loki.samples)
    use_json = bool(total) and json_lines / total >= 0.5
    if total:
        candidates.append(
            Candidate(
                key="use_json_parser",
                value=use_json,
                why="measured by parsing sampled lines",
                verdict=VERIFIED,
                detail=f"{json_lines}/{total} sampled line(s) parsed as JSON",
            )
        )
        evidence_lines.append(f"{json_lines}/{total} sampled line(s) were JSON")

    level_candidate = _level_filter(evidence, oracle, label, use_json=use_json)
    if level_candidate is not None:
        candidates.append(level_candidate)

    module_candidate = _module_regex(evidence, sample_lines)
    if module_candidate is not None:
        candidates.append(module_candidate)
        evidence_lines.append(f"module_regex from the {module_candidate.why}")

    filters = _alert_line_filters(evidence, oracle, label)
    body: list[str] = []
    for candidate in candidates:
        if candidate.accepted:
            body.extend(render.entry(candidate.key, candidate.value))
        else:
            body.extend(render.withheld(candidate))
    filter_lines = render.section("alert_line_filters", tuple(filters))
    if filter_lines:
        body.extend(["", *filter_lines])
        evidence_lines.append(
            f"{len(filters)} line filter(s) extracted from Loki ruler expressions"
        )
    candidates.extend(filters)

    header = render.header(
        "logs_profile.yaml",
        purpose="How to find this stack's logs in Loki: labels, level field, filters.",
        usage=(
            "The retrieve step builds a LogQL query from these fields; "
            "alert_line_filters replace the level gate for named alerts."
        ),
        evidence=evidence_lines or ["no log evidence collected"],
        configure=(
            "service_label must be a real Loki label. alert_line_filters keys "
            "are alert names as Alertmanager sends them."
        ),
        has_withheld=any(not c.accepted for c in candidates),
    )
    return DraftedFile(
        path="logs_profile.yaml",
        content=render.document(header, body),
        candidates=tuple(candidates),
    )


def _level_filter(
    evidence: ScanEvidence, oracle: Oracle, label: str, *, use_json: bool
) -> Candidate | None:
    """Build the level alternation from levels the logs actually use."""
    observed: list[str] = []
    for sample in evidence.loki.samples:
        for level in sample.level_values:
            if level.upper() in _SEVERE_LEVELS and level.upper() not in observed:
                observed.append(level.upper())
    if not observed:
        return None
    ordered = [level for level in _SEVERE_LEVELS if level in observed]
    value = "|".join(ordered)

    field = evidence.loki.level_field or "level"
    if label and use_json:
        query = f'{{{label}=~".+"}} | json | {field}=~"{value}"'
    elif label:
        query = f'{{{label}=~".+"}} |~ "(?i)({value})"'
    else:
        return Candidate(
            key="level_filter",
            value=value,
            why=f"levels observed in sampled lines via the {field!r} field",
            verdict=UNVERIFIED,
            detail="no service label to build a verification query",
        )
    ok, detail = oracle.logql(query)
    return Candidate(
        key="level_filter",
        value=value,
        why=f"levels observed in sampled lines via the {field!r} field",
        verdict=VERIFIED if ok else REJECTED,
        detail=detail,
    )


_LOGGER_SEGMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _module_regex(
    evidence: ScanEvidence, sample_lines: tuple[str, ...]
) -> Candidate | None:
    """Derive a module-hint regex from the common logger-name prefix.

    Works for both full (`com.example.platform.media.MediaService`) and
    abbreviated (`c.p.media.MediaService`) logger names, because the prefix comes
    from the values the stack actually logs.
    """
    names = [
        name
        for sample in evidence.loki.samples
        for name in sample.logger_names
        if "." in name
    ]
    if len(names) < 2:
        return None

    split = [name.split(".") for name in names]
    prefix: list[str] = []
    for segments in zip(*split):
        first = segments[0]
        if all(segment == first for segment in segments) and _LOGGER_SEGMENT.match(first):
            prefix.append(first)
        else:
            break
    # Need a prefix plus room for a module segment after it.
    if not prefix or min(len(parts) for parts in split) <= len(prefix) + 1:
        return None

    pattern = re.escape(".".join(prefix)) + r"\.([a-z0-9_]+)"
    ok, detail = captures_group(pattern, sample_lines)
    return Candidate(
        key="module_regex",
        value=pattern,
        why=f"longest common logger prefix from {len(names)} logger name(s)",
        verdict=VERIFIED if ok else REJECTED,
        detail=detail,
    )


def _alert_line_filters(
    evidence: ScanEvidence, oracle: Oracle, label: str
) -> list[Candidate]:
    """Take each log-based alert's own regex; verify it still matches lines."""
    out: list[Candidate] = []
    seen: set[str] = set()
    for rule in evidence.loki.rules:
        if not rule.name or not rule.line_filters or rule.name in seen:
            continue
        seen.add(rule.name)
        pattern = rule.line_filters[0]
        if label:
            ok, detail = oracle.logql(f'{{{label}=~".+"}} |~ "{pattern}"')
        else:
            ok, detail = False, "no service label to build a verification query"
        out.append(
            Candidate(
                key=rule.name,
                value=pattern,
                why=f"line filter from the Loki ruler expression for {rule.name}",
                verdict=VERIFIED if ok else REJECTED,
                detail=detail,
            )
        )
    return out
