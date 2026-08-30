"""Compare live (or bundled) evidence against a workspace.

Pure over an evidence bundle plus resolved workspace inputs. Live PromQL /
LogQL checks are optional: when no oracle is available they are reported as
warnings rather than silently treated as clean.
"""
from __future__ import annotations

import logging

import yaml

from ..dependency_map import DependencyMap
from ..profile import build_profile
from ..scan.models import ScanEvidence
from ..workspace import Workspace
from .models import ERROR, NOTE, DriftItem, DriftReport

logger = logging.getLogger(__name__)


def detect(
    evidence: ScanEvidence,
    workspace: Workspace,
    *,
    oracle=None,
    window: str = "5m",
) -> DriftReport:
    """Return every drift item for this workspace against this evidence."""
    items: list[DriftItem] = []
    warnings: list[str] = []

    dep_map = _load_map(workspace)
    map_names = set(dep_map.known_services())
    observed = {
        s.name: s
        for s in evidence.findings.services
    }

    # New services with no map node.
    for name, service in sorted(observed.items()):
        if name not in map_names:
            parts = []
            if service.has_metrics:
                parts.append("metrics")
            if service.has_logs:
                parts.append("logs")
            seen = " and ".join(parts) if parts else "evidence"
            items.append(
                DriftItem(
                    kind="new_service",
                    severity=ERROR,
                    name=name,
                    detail=(
                        f"{name} appears in {seen} but has no service_map.yaml node"
                    ),
                )
            )

    # Map nodes that no longer have metrics or logs behind them.
    for name in sorted(map_names):
        service = observed.get(name)
        if service is None or (not service.has_metrics and not service.has_logs):
            items.append(
                DriftItem(
                    kind="gone_service",
                    severity=ERROR,
                    name=name,
                    detail=(
                        f"{name} is in service_map.yaml but has no metrics and no "
                        "logs in the current evidence"
                    ),
                )
            )

    # Alerts with no scenario / runbook coverage.
    ruler_names = {r.name for r in evidence.all_rules() if r.name}
    scenario_alerts = _scenario_alertnames(workspace)
    for name in sorted(ruler_names):
        if name not in scenario_alerts:
            items.append(
                DriftItem(
                    kind="uncovered_alert",
                    severity=ERROR,
                    name=name,
                    detail=f"{name} is defined in a ruler but has no scenario/runbook",
                )
            )

    # Unused runbooks (scenario alerts that no longer exist in rulers) — note.
    if ruler_names:
        for name in sorted(scenario_alerts - ruler_names):
            items.append(
                DriftItem(
                    kind="unused_scenario",
                    severity=NOTE,
                    name=name,
                    detail=(
                        f"scenario covers {name} but no ruler currently defines "
                        "that alert"
                    ),
                )
            )

    # Template / log-selector live checks.
    if oracle is None:
        warnings.append(
            "no live oracle; metrics template and log-selector checks skipped"
        )
    else:
        items.extend(_template_drift(workspace, evidence, oracle, window=window))
        items.extend(_log_selector_drift(workspace, evidence, oracle))

    return DriftReport(
        items=tuple(items),
        workspace=str(workspace.root),
        evidence_at=evidence.generated_at,
        warnings=tuple(warnings),
    )


def _load_map(workspace: Workspace) -> DependencyMap:
    profile = build_profile(
        profile_dir=workspace.profile_dir,
        default_preset=workspace.preset,
    )
    return DependencyMap.load(profile.service_map_path or "")


def _scenario_alertnames(workspace: Workspace) -> set[str]:
    if workspace.scenarios_path is None or not workspace.scenarios_path.is_file():
        return set()
    try:
        data = yaml.safe_load(workspace.scenarios_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("cannot read scenarios: %s", exc)
        return set()
    names: set[str] = set()
    for scenario in data.get("scenarios") or []:
        labels = scenario.get("labels") or {}
        name = str(labels.get("alertname") or "").strip()
        if name:
            names.add(name)
    return names


def _template_drift(workspace: Workspace, evidence: ScanEvidence, oracle, *, window: str):
    """Flag active profile templates that return no data for a probe service."""
    from ..draft.profiles import probe_target

    profile = build_profile(
        profile_dir=workspace.profile_dir,
        default_preset=workspace.preset,
    )
    target = probe_target(evidence)
    if target is None:
        return [
            DriftItem(
                kind="template_check",
                severity=NOTE,
                detail="no probe service available to test metrics templates",
            )
        ]

    items: list[DriftItem] = []
    metrics = profile.metrics
    for name in metrics.service_metrics:
        query = metrics.render(name, service=target.service, window=window)
        if not query:
            continue
        # Retarget if the stack uses a non-service label — the profile may
        # still say service= while the stack labels by job.
        if target.metric_label != "service":
            query = query.replace('service="', f'{target.metric_label}="')
        ok, detail = oracle.promql(query)
        if not ok:
            items.append(
                DriftItem(
                    kind="dead_template",
                    severity=ERROR,
                    name=name,
                    detail=(
                        f"metrics template {name!r} returned no data for "
                        f"{target.service} ({detail})"
                    ),
                )
            )
    return items


def _log_selector_drift(workspace: Workspace, evidence: ScanEvidence, oracle):
    """Flag the workspace service_label when it returns no lines."""
    profile = build_profile(
        profile_dir=workspace.profile_dir,
        default_preset=workspace.preset,
    )
    label = profile.logs.service_label
    if not label:
        return []
    if not evidence.loki.reachable and not evidence.loki.url:
        return [
            DriftItem(
                kind="log_selector",
                severity=NOTE,
                detail="Loki was not in the evidence; log-selector check skipped",
            )
        ]
    ok, detail = oracle.logql(f'{{{label}=~".+"}}')
    if ok:
        return []
    return [
        DriftItem(
            kind="dead_log_selector",
            severity=ERROR,
            name=label,
            detail=f"logs_profile service_label={label!r} returned no lines ({detail})",
        )
    ]
