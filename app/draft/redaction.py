"""Draft `redaction.yaml` from what the sampled logs actually contain.

Additive only: rules accumulate on top of the preset through `extends:`, and
nothing here ever removes one. A pattern is proposed only if it matched real
lines, and its match count is recorded, so the reviewer is accepting evidence
rather than a suggestion.
"""
from __future__ import annotations

from ..scan.models import ScanEvidence
from ..scan.scrub import pattern_specs
from . import render
from .models import UNVERIFIED, VERIFIED, Candidate, DraftedFile


def draft_redaction(evidence: ScanEvidence, *, preset: str) -> DraftedFile:
    hits = {hit.name: hit for hit in evidence.loki.secrets}
    specs = {spec.name: spec for spec in pattern_specs()}

    candidates: list[Candidate] = []
    for name, hit in hits.items():
        spec = specs.get(name)
        if spec is None:
            continue
        rule = {
            "name": name,
            "pattern": spec.pattern,
            "replacement": spec.replacement,
        }
        if spec.flags:
            rule["flags"] = spec.flags
        detail = f"matched {hit.matches} time(s) on {hit.lines} sampled line(s)"
        candidates.append(
            Candidate(
                key=name,
                value=rule,
                why=f"{spec.description} found in sampled logs",
                verdict=VERIFIED if spec.propose_active else UNVERIFIED,
                detail=(
                    detail
                    if spec.propose_active
                    else f"{detail}; high false-positive risk, review before enabling"
                ),
            )
        )
    candidates.sort(key=lambda c: (not c.accepted, c.key))

    body: list[str] = [f"extends: {preset}", ""]
    # `rules:` with every entry commented out parses as null, which would replace
    # the preset's baseline scrubbing with nothing — the agent then refuses to
    # start. So the key itself only appears when something is active.
    if any(c.accepted for c in candidates):
        body.append("rules:")
        for candidate in candidates:
            lines = _rule_lines(candidate.value)
            if candidate.accepted:
                body.append(f"{render.INDENT}# {candidate.detail}")
                body.extend(lines)
            else:
                body.append(f"{render.INDENT}# {candidate.reason()}")
                body.extend(f"# {line}" for line in lines)
    elif candidates:
        body.append("# rules:")
        for candidate in candidates:
            body.append(f"# {render.INDENT}# {candidate.reason()}")
            body.extend(f"# {line}" for line in _rule_lines(candidate.value))

    evidence_lines = (
        [
            f"{c.key}: {c.detail}"
            for c in candidates
        ]
        or ["no sensitive patterns matched the sampled lines"]
    )
    if not evidence.loki.samples:
        evidence_lines = ["no log lines were sampled; nothing could be proposed"]

    header = render.header(
        "redaction.yaml",
        purpose="Regex rules applied to every report, email, and annotation.",
        usage=(
            "Rules accumulate on the preset's baseline secret scrubbing. Zero "
            "resolved rules makes the agent refuse to start."
        ),
        evidence=evidence_lines,
        configure=(
            "Add your own tenant / PII patterns here. Removing a preset rule "
            "requires redefining it by name, which is deliberately awkward."
        ),
        has_withheld=any(not c.accepted for c in candidates),
    )
    return DraftedFile(
        path="redaction.yaml",
        content=render.document(header, body),
        candidates=tuple(candidates),
    )


def _rule_lines(rule: dict) -> list[str]:
    lines = [f"{render.INDENT}- name: {render.scalar(rule['name'])}"]
    for key in ("pattern", "replacement", "flags"):
        if key in rule:
            lines.append(f"{render.INDENT * 2}{key}: {render.scalar(rule[key])}")
    return lines
