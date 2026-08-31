"""Draft `scenarios.yaml` and select the runbooks it needs.

`diag lint` requires a runbook for every scenario and a scenario for every
runbook, so the pair is generated as a unit or not at all. An alert with no
runbook is reported as a gap rather than written as a half-scenario that would
fail lint — and that gap count is the honest measure of corpus coverage.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..scan.models import AlertRule, ScanEvidence
from . import render
from .models import VERIFIED, Candidate, CopiedFile, DraftedFile

logger = logging.getLogger(__name__)

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent
_REFERENCE_RUNBOOKS = _PACKAGE_ROOT / "runbooks"
_REFERENCE_SCENARIOS = _PACKAGE_ROOT / "runbook_scenarios.yaml"

# Tokens too common across runbook names to identify one on their own.
_WEAK_TOKENS = frozenset(
    {
        "runbook",
        "in",
        "logs",
        "log",
        "errors",
        "error",
        "high",
        "low",
        "spike",
        "failures",
        "failure",
        "service",
        "api",
        "rate",
        "time",
        "usage",
        "total",
    }
)


@dataclass(frozen=True)
class Pairing:
    """One alert and the runbook that should answer it."""

    alert: AlertRule
    runbook: str
    how: str


@dataclass(frozen=True)
class ScenarioDraft:
    """The scenario file, the runbooks it needs, and what was left out."""

    scenarios: DraftedFile | None = None
    copied: tuple[CopiedFile, ...] = ()
    runbooks: tuple[DraftedFile, ...] = ()
    uncovered: tuple[str, ...] = ()
    unused: tuple[str, ...] = ()


def reference_corpus() -> tuple[str, ...]:
    """Runbook filenames shipped with the agent, or () when unavailable."""
    if not _REFERENCE_RUNBOOKS.is_dir():
        return ()
    return tuple(sorted(p.name for p in _REFERENCE_RUNBOOKS.glob("runbook-*.md")))


def _upstream_index() -> dict[str, str]:
    """alertname -> runbook, from the reference corpus's own scenarios."""
    if not _REFERENCE_SCENARIOS.is_file():
        return {}
    try:
        data = yaml.safe_load(_REFERENCE_SCENARIOS.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.debug("cannot read reference scenarios: %s", exc)
        return {}
    index: dict[str, str] = {}
    for scenario in data.get("scenarios") or []:
        labels = scenario.get("labels") or {}
        name = str(labels.get("alertname") or "").strip()
        runbook = str(scenario.get("runbook") or "").strip()
        if name and runbook:
            index.setdefault(name, runbook)
    return index


def _tokens(text: str) -> set[str]:
    """Split CamelCase, snake_case and kebab-case into comparable tokens."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    parts = re.split(r"[^A-Za-z0-9]+", spaced)
    return {part.lower() for part in parts if len(part) > 2}


def pair_alerts(
    rules: tuple[AlertRule, ...], corpus: tuple[str, ...]
) -> tuple[tuple[Pairing, ...], tuple[str, ...]]:
    """Match alerts to runbooks; return (pairings, uncovered alert names)."""
    if not corpus:
        return (), tuple(sorted({r.name for r in rules if r.name}))

    upstream = _upstream_index()
    corpus_tokens = {name: _tokens(name.removesuffix(".md")) for name in corpus}
    # A token that appears in exactly one runbook name identifies it alone.
    token_counts: dict[str, int] = {}
    for tokens in corpus_tokens.values():
        for token in tokens:
            token_counts[token] = token_counts.get(token, 0) + 1

    pairings: list[Pairing] = []
    uncovered: list[str] = []
    seen: set[str] = set()
    for rule in rules:
        if not rule.name or rule.name in seen:
            continue
        seen.add(rule.name)

        annotated = Path(rule.runbook).name if rule.runbook else ""
        if annotated and annotated in corpus:
            pairings.append(Pairing(rule, annotated, "runbook annotation on the rule"))
            continue
        if rule.name in upstream and upstream[rule.name] in corpus:
            pairings.append(
                Pairing(rule, upstream[rule.name], "alert name in the reference corpus")
            )
            continue

        match = _fuzzy_match(rule.name, corpus_tokens, token_counts)
        if match:
            runbook, how = match
            pairings.append(Pairing(rule, runbook, how))
        else:
            uncovered.append(rule.name)
    return tuple(pairings), tuple(sorted(uncovered))


def _fuzzy_match(
    alertname: str,
    corpus_tokens: dict[str, set[str]],
    token_counts: dict[str, int],
) -> tuple[str, str] | None:
    alert_tokens = _tokens(alertname)
    best: tuple[int, str, set[str]] | None = None
    for name, tokens in corpus_tokens.items():
        shared = alert_tokens & tokens
        strong = {t for t in shared if t not in _WEAK_TOKENS}
        if not strong:
            continue
        score = len(strong)
        if best is None or score > best[0]:
            best = (score, name, strong)
    if best is None:
        return None
    score, name, strong = best
    if score >= 2:
        return name, f"name overlap on {', '.join(sorted(strong))}"
    only = [t for t in strong if token_counts.get(t) == 1]
    if only:
        return name, f"{only[0]!r} names exactly one runbook"
    return None


def _slug(name: str) -> str:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", name)
    spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "-", spaced)
    return re.sub(r"[^a-z0-9]+", "-", spaced.lower()).strip("-") or "alert"


def _scenario_service(rule: AlertRule, node_names: tuple[str, ...], fallback: str) -> str:
    for service in rule.services:
        if service in node_names:
            return service
    if rule.services:
        return rule.services[0]
    for node in node_names:
        if node.lower() in rule.expr.lower():
            return node
    return fallback or (node_names[0] if node_names else "unknown")


def draft_scenarios(
    evidence: ScanEvidence,
    *,
    node_names: tuple[str, ...] = (),
    fallback_service: str = "",
) -> ScenarioDraft:
    """Pair this stack's alerts with runbooks and emit both, or neither."""
    corpus = reference_corpus()
    pairings, uncovered = pair_alerts(evidence.all_rules(), corpus)
    used = tuple(sorted({p.runbook for p in pairings}))
    unused = tuple(name for name in corpus if name not in used)

    if not pairings:
        return ScenarioDraft(uncovered=uncovered, unused=unused)

    scenarios: list[dict] = []
    candidates: list[Candidate] = []
    for pairing in pairings:
        rule = pairing.alert
        service = _scenario_service(rule, node_names, fallback_service)
        scenarios.append(
            {
                "id": _slug(rule.name),
                "runbook": pairing.runbook,
                "labels": {
                    "alertname": rule.name,
                    "service": service,
                    "severity": rule.severity or "warning",
                },
                "annotations": {
                    "summary": f"{rule.name} drafted from the {rule.source} ruler"
                },
            }
        )
        candidates.append(
            Candidate(
                key=rule.name,
                value=pairing.runbook,
                why=f"paired by {pairing.how}",
                verdict=VERIFIED,
                detail=f"{rule.source} ruler, severity {rule.severity or 'unset'}",
            )
        )

    body = ["version: 1", "", "scenarios:"]
    body.extend(
        yaml.safe_dump(scenarios, sort_keys=False, width=10**6).rstrip("\n").split("\n")
    )

    evidence_lines = [
        f"{len(scenarios)} alert(s) from the Prometheus and Loki rulers",
        f"{len(used)} reference runbook(s) carried into this workspace",
    ]
    if uncovered:
        evidence_lines.append(
            f"{len(uncovered)} alert(s) have no runbook: {', '.join(uncovered[:8])}"
            + (" ..." if len(uncovered) > 8 else "")
        )
    if unused:
        evidence_lines.append(
            f"{len(unused)} reference runbook(s) unused here (not copied)"
        )

    header = render.header(
        "scenarios.yaml",
        purpose="Alert label sets paired with the runbook that should answer them.",
        usage=(
            "`diag lint` checks the pairing both ways; `diag e2e` posts each "
            "scenario at a running agent and asserts its report."
        ),
        evidence=evidence_lines,
        configure=(
            "Alerts with no runbook are listed above but deliberately absent: "
            "add the runbook first, then the scenario, or lint will fail."
        ),
    )
    scenarios_file = DraftedFile(
        path="scenarios.yaml",
        content=render.document(header, body),
        candidates=tuple(candidates),
    )

    copied: list[CopiedFile] = []
    runbook_files: list[DraftedFile] = []
    for pairing in pairings:
        source = _REFERENCE_RUNBOOKS / pairing.runbook
        target = f"runbooks/{pairing.runbook}"
        if any(f.path == target for f in runbook_files):
            continue
        try:
            content = source.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("cannot read reference runbook %s: %s", source, exc)
            continue
        runbook_files.append(DraftedFile(path=target, content=content))
        copied.append(
            CopiedFile(
                path=target,
                source=str(source),
                reason=f"answers {pairing.alert.name} ({pairing.how})",
            )
        )

    return ScenarioDraft(
        scenarios=scenarios_file,
        copied=tuple(copied),
        runbooks=tuple(runbook_files),
        uncovered=uncovered,
        unused=unused,
    )
