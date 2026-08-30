"""LLM-authored runbook skeletons for alerts with no reference runbook.

Each skeleton is a unit with its scenario (lint bijection) and carries an
explicit DRAFT marker that ``diag lint`` rejects until a human edits it. That
keeps the corpus backlog measurable without pretending an invented runbook is
ready for production.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, Field

from ..scan.models import AlertRule, ScanEvidence
from . import render
from .grounding import Allowlist, build_allowlist, validate_runbook_body
from .models import REJECTED, UNVERIFIED, VERIFIED, Candidate, DraftedFile

logger = logging.getLogger(__name__)

# Shared with corpus_lint — do not rename without updating both sides.
DRAFT_MARKER = (
    "<!-- DRAFT: edit before relying on this runbook; "
    "remove this marker when ready -->"
)

InvokeFn = Callable[[list], Any]


class SkeletonDraft(BaseModel):
    meaning: str = Field(description="What this alert indicates in this stack's terms")
    first_checks: list[str] = Field(
        description="3–5 concrete first checks (PromQL, LogQL, docker), using only inventory names"
    )
    common_causes: list[str] = Field(
        description="2–4 typical root causes with evidence signals"
    )
    blast_radius: str = Field(
        description="Which services or dependencies are likely affected"
    )


@dataclass(frozen=True)
class SkeletonResult:
    runbooks: tuple[DraftedFile, ...] = ()
    scenarios: tuple[dict, ...] = ()
    candidates: tuple[Candidate, ...] = ()
    drafted_alerts: tuple[str, ...] = ()


_SYSTEM = """\
You are drafting a hypotheses-only runbook skeleton for a diagnostic agent.

Rules:
- Use ONLY service names and hosts from the inventory.
- Never claim you restarted, fixed, or executed anything.
- Prefer concrete PromQL / LogQL / docker checks tied to the alert expression.
- Keep each first_check to one line.
- Output the structured fields only.
"""


def _slug(name: str) -> str:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", name)
    spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "-", spaced)
    return re.sub(r"[^a-z0-9]+", "-", spaced.lower()).strip("-") or "alert"


def runbook_filename(alertname: str) -> str:
    return f"runbook-{_slug(alertname)}.md"


def draft_skeletons(
    evidence: ScanEvidence,
    uncovered: tuple[str, ...],
    *,
    node_names: tuple[str, ...] = (),
    fallback_service: str = "",
    extra_urls: tuple[str, ...] = (),
    invoke: InvokeFn | None = None,
    max_alerts: int = 12,
) -> SkeletonResult:
    """Author draft runbooks + scenarios for uncovered alerts."""
    if not uncovered:
        return SkeletonResult()

    rules_by_name = {r.name: r for r in evidence.all_rules() if r.name}
    allowlist = build_allowlist(
        evidence, node_names=node_names, extra_urls=extra_urls
    )
    invoke_fn = invoke or _default_invoke

    runbooks: list[DraftedFile] = []
    scenarios: list[dict] = []
    candidates: list[Candidate] = []
    drafted: list[str] = []

    for name in uncovered[:max_alerts]:
        rule = rules_by_name.get(name) or AlertRule(name=name, source="unknown")
        service = _service_for(rule, node_names, fallback_service)
        try:
            skeleton = _coerce(
                invoke_fn(_messages(evidence, rule, service, allowlist))
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("skeleton LLM call failed for %s: %s", name, exc)
            skeleton = _fallback_skeleton(rule, service)
            candidates.append(
                Candidate(
                    key=name,
                    value=runbook_filename(name),
                    why="LLM failed; wrote template-only skeleton",
                    verdict=UNVERIFIED,
                    detail=str(exc),
                )
            )
        else:
            failures = validate_runbook_body(
                "\n".join(
                    [skeleton.meaning, skeleton.blast_radius, *skeleton.first_checks]
                ),
                allowlist,
            )
            if failures:
                # Keep a safe template rather than ungrounded prose.
                detail = "; ".join(str(f) for f in failures)
                logger.info("skeleton for %s failed grounding: %s", name, detail)
                skeleton = _fallback_skeleton(rule, service)
                candidates.append(
                    Candidate(
                        key=name,
                        value=runbook_filename(name),
                        why="LLM prose failed grounding; wrote template-only skeleton",
                        verdict=REJECTED,
                        detail=detail,
                    )
                )
            else:
                candidates.append(
                    Candidate(
                        key=name,
                        value=runbook_filename(name),
                        why="LLM skeleton grounded in evidence; marked DRAFT for lint",
                        verdict=VERIFIED,
                        detail="lint will reject until the DRAFT marker is removed",
                    )
                )

        filename = runbook_filename(name)
        body = render_skeleton(rule, skeleton, service=service)
        runbooks.append(
            DraftedFile(
                path=f"runbooks/{filename}",
                content=body,
                candidates=(),
            )
        )
        scenarios.append(
            {
                "id": _slug(name),
                "runbook": filename,
                "labels": {
                    "alertname": name,
                    "service": service,
                    "severity": rule.severity or "warning",
                },
                "annotations": {
                    "summary": f"{name} draft skeleton (edit before relying on it)"
                },
            }
        )
        drafted.append(name)

    return SkeletonResult(
        runbooks=tuple(runbooks),
        scenarios=tuple(scenarios),
        candidates=tuple(candidates),
        drafted_alerts=tuple(drafted),
    )


def render_skeleton(
    rule: AlertRule, skeleton: SkeletonDraft, *, service: str
) -> str:
    """Fill the runbook template; always includes DRAFT_MARKER and Hypotheses-only."""
    checks = "\n".join(
        f"{i}. {line}" for i, line in enumerate(skeleton.first_checks or ["<!-- add a first check -->"], 1)
    )
    causes = "\n".join(
        f"- {line}" for line in (skeleton.common_causes or ["<!-- add a common cause -->"])
    )
    expr = rule.expr or f"(expression for {rule.name} not captured)"
    duration = rule.duration or "for the configured duration"
    return (
        f"{DRAFT_MARKER}\n"
        f"# Runbook: {rule.name} ({(skeleton.meaning or rule.name).split('.')[0][:72]})\n"
        "\n"
        f"**Alert:** `{expr}` for `{duration}`.\n"
        "\n"
        "## Meaning\n"
        f"{skeleton.meaning.strip() or '<!-- What this alert indicates -->'}\n"
        "\n"
        "## First checks\n"
        f"{checks}\n"
        "\n"
        "## Common causes\n"
        f"{causes}\n"
        "\n"
        "## Blast radius\n"
        f"{skeleton.blast_radius.strip() or f'Primarily {service}; confirm neighbours in service_map.yaml.'}\n"
        "\n"
        "## Hypotheses-only\n"
        "This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human\n"
        "confirms and acts.\n"
    )


def merge_scenarios_file(
    existing: DraftedFile | None,
    extra: tuple[dict, ...],
    *,
    evidence_note: str,
) -> DraftedFile | None:
    """Append skeleton scenarios onto the deterministic scenarios file."""
    if not extra and existing is None:
        return None
    import yaml

    scenarios: list[dict] = []
    candidates: list[Candidate] = []
    if existing is not None:
        # Re-parse body after the header comment block.
        data = yaml.safe_load(
            "\n".join(
                line
                for line in existing.content.splitlines()
                if not line.startswith("#")
            )
        ) or {}
        scenarios.extend(data.get("scenarios") or [])
        candidates.extend(existing.candidates)
    scenarios.extend(extra)

    body = ["version: 1", "", "scenarios:"]
    body.extend(
        yaml.safe_dump(scenarios, sort_keys=False, width=10**6).rstrip("\n").split("\n")
    )
    evidence_lines = [
        evidence_note,
        f"{len(extra)} draft skeleton scenario(s) added for uncovered alerts",
    ]
    header = render.header(
        "scenarios.yaml",
        purpose="Alert label sets paired with the runbook that should answer them.",
        usage=(
            "`diag lint` checks the pairing both ways. Draft skeletons fail lint "
            "until their DRAFT marker is removed."
        ),
        evidence=evidence_lines,
        configure=(
            "Edit each DRAFT runbook, remove its marker, then re-run diag lint."
        ),
    )
    return DraftedFile(
        path="scenarios.yaml",
        content=render.document(header, body),
        candidates=tuple(candidates),
    )


def _service_for(
    rule: AlertRule, node_names: tuple[str, ...], fallback: str
) -> str:
    for service in rule.services:
        if service in node_names:
            return service
    if rule.services:
        return rule.services[0]
    for node in node_names:
        if node.lower() in (rule.expr or "").lower():
            return node
    return fallback or (node_names[0] if node_names else "unknown")


def _fallback_skeleton(rule: AlertRule, service: str) -> SkeletonDraft:
    return SkeletonDraft(
        meaning=(
            f"{rule.name} fired on {service}. Confirm the expression against "
            "live metrics/logs before acting."
        ),
        first_checks=[
            f'Check recent logs: curl -sG \'http://loki:3100/loki/api/v1/query_range\' '
            f'--data-urlencode \'query={{service="{service}"}}\' | head',
            f"Confirm the alert expression still matches: {rule.expr or rule.name}",
            f"Inspect neighbours of {service} in service_map.yaml",
        ],
        common_causes=[
            "Transient dependency failure visible in the alerted service's logs",
            "Metric or log threshold crossed during a deploy or traffic spike",
        ],
        blast_radius=f"Primarily {service}; check its downstream in service_map.yaml.",
    )


def _messages(
    evidence: ScanEvidence,
    rule: AlertRule,
    service: str,
    allowlist: Allowlist,
) -> list[dict]:
    import json

    inventory = {
        "alert": {
            "name": rule.name,
            "source": rule.source,
            "severity": rule.severity,
            "expr": rule.expr,
            "duration": rule.duration,
            "services": list(rule.services),
        },
        "service": service,
        "allowlist_services": sorted(allowlist.services)[:40],
        "prometheus_url": evidence.prometheus.url,
        "loki_url": evidence.loki.url,
        "service_label": evidence.loki.service_label or "service",
    }
    return [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "human",
            "content": (
                "Draft a runbook skeleton from this inventory JSON. Quote only "
                "names that appear here:\n"
                f"{json.dumps(inventory, indent=2)}"
            ),
        },
    ]


def _coerce(result: Any) -> SkeletonDraft:
    if isinstance(result, SkeletonDraft):
        return result
    if isinstance(result, dict):
        return SkeletonDraft.model_validate(result)
    if hasattr(result, "model_dump"):
        return SkeletonDraft.model_validate(result.model_dump())
    raise TypeError(f"unexpected skeleton type: {type(result)}")


def _default_invoke(messages: list) -> SkeletonDraft:
    from langchain_core.messages import HumanMessage, SystemMessage

    from ..llm import get_chat_model

    model = get_chat_model(for_structured_output=True).with_structured_output(
        SkeletonDraft
    )
    lc = []
    for msg in messages:
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "type", "")
        content = (
            msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
        )
        if role in ("system", "SystemMessage"):
            lc.append(SystemMessage(content=content))
        else:
            lc.append(HumanMessage(content=content))
    result = model.invoke(lc)
    return _coerce(result)
