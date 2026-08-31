"""Render an evidence bundle as text.

Deliberately plain: `key=value` lines and small aligned tables, matching the
other ``diag`` commands, and ASCII only so it survives a Windows console.
"""
from __future__ import annotations

from .models import ScanEvidence

_RULE = "-" * 72


def render(evidence: ScanEvidence, *, verbose: bool = False) -> str:
    lines: list[str] = []
    lines.extend(_header(evidence))
    lines.extend(_sources(evidence))
    lines.extend(_services(evidence))
    lines.extend(_naming(evidence, verbose=verbose))
    lines.extend(_alerts(evidence, verbose=verbose))
    lines.extend(_logs(evidence))
    lines.extend(_secrets(evidence))
    lines.extend(_findings(evidence))
    return "\n".join(lines)


def _section(title: str) -> list[str]:
    return ["", title, _RULE]


def _header(evidence: ScanEvidence) -> list[str]:
    return [
        f"diag scan (agent {evidence.agent_version}, schema {evidence.schema})",
        f"generated_at={evidence.generated_at}",
        f"workspace={evidence.workspace or '(none)'}",
    ]


def _sources(evidence: ScanEvidence) -> list[str]:
    out = _section("sources")
    prom = evidence.prometheus
    if prom.reachable:
        out.append(
            f"ok   prometheus {prom.url} version={prom.version or 'unknown'} "
            f"metrics={prom.metric_count} targets={len(prom.targets)} "
            f"rules={len(prom.rules)}"
        )
    else:
        out.append(f"FAIL prometheus {prom.url or '(unset)'}")

    loki = evidence.loki
    if loki.reachable:
        out.append(
            f"ok   loki {loki.url} labels={len(loki.label_names)} "
            f"service_label={loki.service_label or '(unknown)'} "
            f"rules={len(loki.rules)}"
        )
    else:
        # An unset URL was a choice; a set one that did not answer is a problem
        # worth distinguishing, even though neither fails the scan.
        out.append(f"warn loki {loki.url} did not answer" if loki.url else "skip loki (unset)")

    am = evidence.alertmanager
    if am.reachable:
        out.append(
            f"ok   alertmanager {am.url} version={am.version or 'unknown'} "
            f"receivers={len(am.receivers)} firing={sum(am.firing.values())}"
        )
    elif am.url:
        out.append(f"warn alertmanager {am.url} did not answer")
    else:
        out.append("skip alertmanager (unset)")

    for source, label in (
        (prom, "prometheus"),
        (loki, "loki"),
        (am, "alertmanager"),
    ):
        for note in source.notes:
            out.append(f"     note ({label}): {note}")
    return out


def _services(evidence: ScanEvidence) -> list[str]:
    services = evidence.findings.services
    out = _section(f"service candidates ({len(services)})")
    if not services:
        out.append("(none identified)")
        return out

    width = max(len(s.name) for s in services)
    for service in services:
        parts = [
            f"{service.name.ljust(width)}",
            "metrics=yes" if service.has_metrics else "metrics=no ",
            "logs=yes" if service.has_logs else "logs=no ",
        ]
        if service.kind_hints:
            parts.append(f"kind~{'/'.join(service.kind_hints)}")
        if service.log_services_hint:
            parts.append(f"logs_under={','.join(service.log_services_hint)}")
        out.append("  " + "  ".join(parts))
    out.append("")
    out.append("kind~ and logs_under are hints from evidence, not decisions.")
    return out


def _naming(evidence: ScanEvidence, *, verbose: bool) -> list[str]:
    markers = [m for m in evidence.findings.naming_markers if m.present or verbose]
    out = _section("metric naming markers")
    if not markers:
        out.append("(no known marker metrics found)")
        return out
    width = max(len(m.metric) for m in markers)
    for marker in markers:
        state = "present" if marker.present else "absent "
        out.append(f"  {marker.metric.ljust(width)}  {state}  {marker.means}")
    return out


def _alerts(evidence: ScanEvidence, *, verbose: bool) -> list[str]:
    rules = evidence.all_rules()
    prom_count = len(evidence.prometheus.rules)
    loki_count = len(evidence.loki.rules)
    out = _section(
        f"alerts ({len(rules)}: {prom_count} prometheus, {loki_count} loki ruler)"
    )
    if not rules:
        out.append("(no alerting rules defined)")
        return out

    firing = evidence.alertmanager.firing
    uncovered = set(evidence.findings.uncovered_alerts)
    width = max(len(r.name) for r in rules)
    shown = rules if verbose else rules[:40]
    for rule in shown:
        parts = [rule.name.ljust(width), f"[{rule.source}]"]
        parts.append(f"severity={rule.severity or '(none)'}")
        if rule.runbook:
            parts.append(f"runbook={rule.runbook}")
        elif rule.name in uncovered:
            parts.append("runbook=(gap)")
        if rule.name in firing:
            parts.append(f"firing={firing[rule.name]}")
        if rule.line_filters:
            parts.append(f"line_filters={len(rule.line_filters)}")
        out.append("  " + "  ".join(parts))
    if len(shown) < len(rules):
        out.append(f"  ... {len(rules) - len(shown)} more (use --verbose)")

    covered = evidence.findings.covered_alerts
    if covered or uncovered:
        out.append("")
        out.append(
            f"workspace coverage: {len(covered)} alert(s) have a scenario, "
            f"{len(uncovered)} do not"
        )
        if uncovered:
            listed = sorted(uncovered)
            preview = ", ".join(listed if verbose else listed[:10])
            suffix = "" if verbose or len(listed) <= 10 else ", ..."
            out.append(f"  no scenario: {preview}{suffix}")
    return out


def _logs(evidence: ScanEvidence) -> list[str]:
    loki = evidence.loki
    out = _section("log shape")
    if not loki.reachable:
        out.append("(loki not scanned)")
        return out

    for label in sorted(loki.label_values):
        values = loki.label_values[label]
        marker = " <- service label" if label == loki.service_label else ""
        out.append(f"  {label}: {len(values)} value(s){marker}")

    if not loki.samples:
        out.append("  (no lines sampled)")
        return out

    total = sum(s.line_count for s in loki.samples)
    json_total = sum(s.json_lines for s in loki.samples)
    pct = int(round(100 * json_total / total)) if total else 0
    out.append("")
    out.append(f"  sampled {total} line(s) across {len(loki.samples)} stream(s)")
    out.append(f"  json parseable: {pct}% -> use_json_parser={str(pct >= 50).lower()}")
    out.append(f"  level field: {loki.level_field or '(none detected)'}")

    levels: dict[str, None] = {}
    prefixes: dict[str, int] = {}
    for sample in loki.samples:
        for level in sample.level_values:
            levels.setdefault(level, None)
        for logger_name in sample.logger_names:
            prefix = ".".join(logger_name.split(".")[:3])
            if prefix:
                prefixes[prefix] = prefixes.get(prefix, 0) + 1
    if levels:
        out.append(f"  levels seen: {', '.join(sorted(levels))}")
    if prefixes:
        ranked = sorted(prefixes.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        out.append(
            "  logger prefixes: "
            + ", ".join(f"{name} ({count})" for name, count in ranked)
        )
    return out


def _secrets(evidence: ScanEvidence) -> list[str]:
    hits = evidence.loki.secrets
    out = _section("sensitive patterns in the log sample")
    if not hits:
        out.append("(none matched, or no lines sampled)")
        return out
    width = max(len(h.name) for h in hits)
    for hit in hits:
        out.append(
            f"  {hit.name.ljust(width)}  {hit.lines} line(s), "
            f"{hit.matches} match(es)  {hit.description}"
        )
    out.append("")
    out.append(
        "These are candidates for redaction.yaml. Counts come from raw lines; "
        "anything held in this bundle is already scrubbed. `diag draft` writes "
        "before→after samples to redaction-review.md (matched spans marked «name»)."
    )
    return out


def _findings(evidence: ScanEvidence) -> list[str]:
    notes = evidence.findings.notes
    if not notes:
        return []
    out = _section("observations")
    for note in notes:
        out.append(f"  - {note}")
    return out
