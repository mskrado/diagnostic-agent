"""Orchestrate a draft: collect evidence, verify candidates, assemble files."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from ..scan.collect import ScanOptions, collect_evidence
from ..scan.models import ScanEvidence
from . import alerts, profiles, redaction, render, topology
from .models import DraftedFile, DraftResult
from .verify import LiveOracle, Oracle

logger = logging.getLogger(__name__)

InvokeFn = Callable[[list], Any]


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
    # Phase 3 — opt-in LLM authoring. Default stays fully deterministic.
    use_llm: bool = False
    llm_prompt: bool = True
    llm_runbooks: bool = True
    # Injected by tests; production resolves the chat model lazily.
    prompt_invoke: InvokeFn | None = None
    runbook_invoke: InvokeFn | None = None


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
    redaction_file = redaction.draft_redaction(evidence, preset=preset)
    files.append(redaction_file)
    review = redaction.draft_redaction_review(evidence)
    if review is not None:
        files.append(review)

    scenario_draft = alerts.draft_scenarios(
        evidence,
        node_names=tuple(node.name for node in nodes),
        fallback_service=target.service if target else "",
    )
    scenarios_file = scenario_draft.scenarios
    runbook_files = list(scenario_draft.runbooks)
    uncovered = scenario_draft.uncovered
    draft_runbook_alerts: tuple[str, ...] = ()

    if options.use_llm and options.llm_runbooks and uncovered:
        from . import runbook_llm

        skeletons = runbook_llm.draft_skeletons(
            evidence,
            uncovered,
            node_names=tuple(node.name for node in nodes),
            fallback_service=target.service if target else "",
            extra_urls=(
                options.prometheus_url,
                options.loki_url,
                options.alertmanager_url,
            ),
            invoke=options.runbook_invoke,
        )
        runbook_files.extend(skeletons.runbooks)
        scenarios_file = runbook_llm.merge_scenarios_file(
            scenarios_file,
            skeletons.scenarios,
            evidence_note=(
                f"{len(scenario_draft.copied)} reference runbook(s) + "
                f"{len(skeletons.drafted_alerts)} DRAFT skeleton(s)"
            ),
        )
        draft_runbook_alerts = skeletons.drafted_alerts
        uncovered = tuple(
            name for name in uncovered if name not in skeletons.drafted_alerts
        )
        if skeletons.candidates and scenarios_file is not None:
            scenarios_file = DraftedFile(
                path=scenarios_file.path,
                content=scenarios_file.content,
                candidates=scenarios_file.candidates + skeletons.candidates,
            )

    if scenarios_file is not None:
        files.append(scenarios_file)
        files.extend(runbook_files)
    elif evidence.all_rules():
        warnings.append(
            f"none of the {len(evidence.all_rules())} alert(s) matched a reference "
            "runbook, so no scenarios were drafted"
            + (" (pass --llm to skeleton the rest)" if not options.use_llm else "")
        )
    else:
        warnings.append("no alerting rules found, so no scenarios were drafted")

    if options.use_llm and options.llm_prompt:
        from . import prompt_llm

        files.append(
            prompt_llm.author_prompt_profile(
                evidence,
                nodes,
                preset=preset,
                extra_urls=(
                    options.prometheus_url,
                    options.loki_url,
                    options.alertmanager_url,
                ),
                invoke=options.prompt_invoke,
            )
        )

    files.append(
        _agent_manifest(
            preset,
            options,
            has_runbooks=any(f.path.startswith("runbooks/") for f in files),
            has_scenarios=any(f.path == "scenarios.yaml" for f in files),
        )
    )

    if draft_runbook_alerts:
        warnings.append(
            f"{len(draft_runbook_alerts)} DRAFT runbook(s) need a human edit before "
            "diag lint will pass: " + ", ".join(draft_runbook_alerts[:8])
            + (" ..." if len(draft_runbook_alerts) > 8 else "")
        )

    return DraftResult(
        files=tuple(files),
        copied=scenario_draft.copied,
        preset=preset,
        preset_scores=scores,
        uncovered_alerts=uncovered,
        unused_runbooks=scenario_draft.unused,
        draft_runbooks=draft_runbook_alerts,
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
            (
                "LLM authoring enabled (--llm)"
                if options.use_llm
                else "deterministic draft (no LLM)"
            ),
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
    draft_count = 0
    for drafted in result.files:
        if drafted.path.startswith("runbooks/"):
            runbook_count += 1
            if "DRAFT:" in drafted.content:
                draft_count += 1
            continue
        accepted = len(drafted.accepted)
        withheld = len(drafted.withheld)
        suffix = f", {withheld} withheld" if withheld else ""
        lines.append(f"  {drafted.path:<28} {accepted} verified value(s){suffix}")
    if runbook_count:
        detail = f"{runbook_count} runbook(s)"
        if draft_count:
            detail += f" ({draft_count} marked DRAFT)"
        lines.append(f"  {'runbooks/':<28} {detail}")

    withheld_all = [c for c in result.all_candidates() if not c.accepted]
    if withheld_all:
        lines.extend(["", "withheld (written commented out)", rule])
        for candidate in withheld_all:
            lines.append(f"  {candidate.key}: {candidate.reason()}")

    if result.copied:
        lines.extend(["", "runbooks carried over", rule])
        for copy in result.copied:
            lines.append(f"  {copy.path}: {copy.reason}")

    if result.draft_runbooks:
        lines.extend(["", "DRAFT runbooks (lint will reject until edited)", rule])
        for name in result.draft_runbooks:
            lines.append(f"  {name}")
        lines.append("")
        lines.append(
            "Edit each skeleton, remove the DRAFT marker, then re-run diag lint."
        )

    if result.uncovered_alerts:
        lines.extend(["", "alerts with no runbook", rule])
        for name in result.uncovered_alerts:
            lines.append(f"  {name}")
        lines.append("")
        lines.append(
            "Write a runbook for these, then add the scenario. This is the "
            "corpus backlog, measured."
            + (
                " Pass --llm to skeleton them as DRAFTs."
                if not result.draft_runbooks
                else ""
            )
        )

    if result.unused_runbooks:
        lines.extend(["", "reference runbooks not used here", rule])
        lines.append("  " + ", ".join(result.unused_runbooks))

    if result.warnings:
        lines.extend(["", "warnings", rule])
        for warning in result.warnings:
            lines.append(f"  - {warning}")

    return "\n".join(lines)
