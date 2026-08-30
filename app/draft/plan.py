"""Orchestrate a draft: collect evidence, verify candidates, assemble files."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ..scan.collect import ScanOptions, collect_evidence
from ..scan.models import ScanEvidence
from . import alerts, profiles, redaction, render, topology
from .models import DraftedFile, DraftResult
from .verify import LiveOracle, Oracle

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DraftOptions:
    prometheus_url: str
    loki_url: str = ""
    alertmanager_url: str = ""
    timeout: float = 10.0
    lookback_minutes: int = 60
    sample_lines: int = 300
    max_services: int = 12
    window: str = "5m"
    workspace: str = ""
    agent_version: str = ""


def scan_for_draft(options: DraftOptions) -> ScanEvidence:
    """Collect evidence with log lines kept in memory.

    Drafting needs the lines themselves — a `module_regex` cannot be checked
    against a summary — but they are never written to disk from here.
    """
    return collect_evidence(
        ScanOptions(
            prometheus_url=options.prometheus_url,
            loki_url=options.loki_url,
            alertmanager_url=options.alertmanager_url,
            timeout=options.timeout,
            lookback_minutes=options.lookback_minutes,
            sample_lines=options.sample_lines,
            max_services=options.max_services,
            keep_lines=True,
            workspace=options.workspace,
        )
    )


def draft(
    evidence: ScanEvidence,
    options: DraftOptions,
    oracle: Oracle | None = None,
) -> DraftResult:
    """Turn evidence into files. ``oracle`` defaults to the live stack."""
    if oracle is None:
        oracle = LiveOracle(
            options.prometheus_url,
            options.loki_url,
            timeout=options.timeout,
            lookback_minutes=options.lookback_minutes,
        )

    warnings: list[str] = []
    files: list[DraftedFile] = []

    nodes, node_candidates = topology.build_nodes(evidence, oracle)
    if not nodes:
        warnings.append(
            "no service had metrics or logs behind it, so service_map.yaml has no "
            "nodes; check the scan report before trusting this draft"
        )
    files.append(topology.render_service_map(nodes, node_candidates, evidence))

    target = profiles.probe_target(evidence)
    scores = (
        profiles.score_presets(evidence, oracle, target, window=options.window)
        if target
        else ()
    )
    preset = profiles.choose_preset(scores)
    if scores and scores[0].verified == 0:
        warnings.append(
            "no preset's metric templates returned data; metrics_profile.yaml "
            f"falls back to {preset} with every template flagged"
        )

    kinds = tuple(dict.fromkeys(node.kind for node in nodes))
    files.append(
        profiles.draft_metrics_profile(
            evidence,
            oracle,
            preset=preset,
            target=target,
            kinds=kinds,
            window=options.window,
            scores=scores,
        )
    )
    files.append(profiles.draft_logs_profile(evidence, oracle))
    files.append(redaction.draft_redaction(evidence, preset=preset))

    scenario_draft = alerts.draft_scenarios(
        evidence,
        node_names=tuple(node.name for node in nodes),
        fallback_service=target.service if target else "",
    )
    if scenario_draft.scenarios is not None:
        files.append(scenario_draft.scenarios)
        files.extend(scenario_draft.runbooks)
    elif evidence.all_rules():
        warnings.append(
            f"none of the {len(evidence.all_rules())} alert(s) matched a reference "
            "runbook, so no scenarios were drafted"
        )
    else:
        warnings.append("no alerting rules found, so no scenarios were drafted")

    files.append(
        _agent_manifest(
            preset,
            options,
            has_runbooks=any(f.path.startswith("runbooks/") for f in files),
            has_scenarios=any(f.path == "scenarios.yaml" for f in files),
        )
    )

    return DraftResult(
        files=tuple(files),
        copied=scenario_draft.copied,
        preset=preset,
        preset_scores=scores,
        uncovered_alerts=scenario_draft.uncovered,
        unused_runbooks=scenario_draft.unused,
        warnings=tuple(warnings),
    )


def _agent_manifest(
    preset: str,
    options: DraftOptions,
    *,
    has_runbooks: bool,
    has_scenarios: bool,
) -> DraftedFile:
    """The manifest only declares paths that exist: a declared missing path is
    a hard workspace error, and a draft with no scenarios is a valid outcome.

    No `agent_version` pin. The drafting version is recorded in the header as
    provenance; pinning it would make every later upgrade warn for no reason.
    """
    body = ["schema: 1", f"extends: {preset}"]
    if has_runbooks:
        body.append("runbooks: runbooks")
    if has_scenarios:
        body.append("scenarios: scenarios.yaml")
    header = render.header(
        "agent.yaml",
        purpose="Workspace manifest: which preset to extend and where each piece lives.",
        usage="Every `diag` command resolves its inputs from this file.",
        evidence=[
            f"preset {preset} selected by query score",
            f"drafted by agent {options.agent_version or 'unknown'}",
        ],
        configure=(
            "Point `profile:` at a subdirectory if you prefer the profile split "
            "out; this draft keeps everything in one flat directory."
        ),
    )
    return DraftedFile(path="agent.yaml", content=render.document(header, body))


def report(result: DraftResult, evidence: ScanEvidence) -> str:
    """Human-readable summary of what was drafted and what was withheld."""
    rule = "-" * 72
    lines = [
        f"diag draft (agent {evidence.agent_version})",
        f"preset={result.preset}",
    ]

    lines.extend(["", "preset scoring", rule])
    if result.preset_scores:
        for score in result.preset_scores:
            marker = " <- chosen" if score.name == result.preset else ""
            lines.append(
                f"  {score.name:<24} {score.verified}/{score.total} templates "
                f"returned data (probe: {score.probe_service}){marker}"
            )
    else:
        lines.append("  (no probe service; scoring skipped)")

    lines.extend(["", "files", rule])
    runbook_count = 0
    for drafted in result.files:
        if drafted.path.startswith("runbooks/"):
            runbook_count += 1
            continue
        accepted = len(drafted.accepted)
        withheld = len(drafted.withheld)
        suffix = f", {withheld} withheld" if withheld else ""
        lines.append(f"  {drafted.path:<28} {accepted} verified value(s){suffix}")
    if runbook_count:
        lines.append(f"  {'runbooks/':<28} {runbook_count} runbook(s) carried over")

    withheld_all = [c for c in result.all_candidates() if not c.accepted]
    if withheld_all:
        lines.extend(["", "withheld (written commented out)", rule])
        for candidate in withheld_all:
            lines.append(f"  {candidate.key}: {candidate.reason()}")

    if result.copied:
        lines.extend(["", "runbooks carried over", rule])
        for copy in result.copied:
            lines.append(f"  {copy.path}: {copy.reason}")

    if result.uncovered_alerts:
        lines.extend(["", "alerts with no runbook", rule])
        for name in result.uncovered_alerts:
            lines.append(f"  {name}")
        lines.append("")
        lines.append(
            "Write a runbook for these, then add the scenario. This is the "
            "corpus backlog, measured."
        )

    if result.unused_runbooks:
        lines.extend(["", "reference runbooks not used here", rule])
        lines.append("  " + ", ".join(result.unused_runbooks))

    if result.warnings:
        lines.extend(["", "warnings", rule])
        for warning in result.warnings:
            lines.append(f"  - {warning}")

    return "\n".join(lines)
