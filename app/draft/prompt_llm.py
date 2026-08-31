"""LLM authoring of ``prompt_profile.yaml``, grounded then validated.

Codifies ``docs/PROMPT_PROFILE_AUTHORING.md`` as the system prompt. The model
sees only scrubbed evidence; the mechanical validator in :mod:`grounding`
rejects invented names. One retry with the failure list, then withhold.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Protocol

from pydantic import BaseModel, Field

from ..scan.models import ScanEvidence
from . import render
from .grounding import Allowlist, build_allowlist, validate_prompt_profile
from .models import REJECTED, VERIFIED, Candidate, DraftedFile
from .topology import Node

logger = logging.getLogger(__name__)

# Injected in tests; production uses LangChain structured output.
InvokeFn = Callable[[list], Any]


class PromptDraft(BaseModel):
    """Structured fields matching ``PromptProfile`` / the playbook schema."""

    platform_description: str = Field(
        description=(
            "One dense paragraph: architecture, backing stores, observability "
            "endpoints, and hard measurement gaps. Under ~2000 characters."
        )
    )
    tool_run_hints: str = Field(
        description=(
            "Run context, allowlist matrix (alert service= ↔ names), hard rules, "
            "8–15 golden copy-paste commands, forbidden invented names, and "
            "remediation honesty. Commands must use only names from the inventory."
        )
    )


_SYSTEM = """\
You are authoring prompt_profile.yaml for a diagnostic-agent host workspace.

Follow docs/PROMPT_PROFILE_AUTHORING.md exactly:
1. Use ONLY names, hosts, ports, and service labels from the inventory JSON.
2. Build platform_description (architecture + measurement gaps). Keep it under
   2000 characters.
3. Build tool_run_hints with, in order: where to run commands, allowlist matrix,
   hard rules, golden curl/docker commands, forbidden invented names, and
   remediation honesty (suggest restarts only as operator steps; never claim
   you executed anything).
4. Do not put secrets, passwords, or credentials in the file.
5. Do not invent compose service names, container names, or ports.
6. Prefer in-network DNS names from the inventory (prometheus, loki, service
   names) and localhost equivalents only when noting host access.

Output the structured fields only.
"""


class ChatFactory(Protocol):
    def __call__(self) -> Any: ...


def _default_invoke(messages: list) -> PromptDraft:
    from langchain_core.messages import HumanMessage, SystemMessage

    from ..llm import get_chat_model

    model = get_chat_model(for_structured_output=True).with_structured_output(
        PromptDraft
    )
    # Rebuild as LangChain messages if callers passed plain dicts.
    lc_messages = []
    for msg in messages:
        role = getattr(msg, "type", None) or (
            msg.get("role") if isinstance(msg, dict) else None
        )
        content = getattr(msg, "content", None) or (
            msg.get("content") if isinstance(msg, dict) else str(msg)
        )
        if role in ("system", "SystemMessage"):
            lc_messages.append(SystemMessage(content=content))
        else:
            lc_messages.append(HumanMessage(content=content))
    if not lc_messages:
        lc_messages = [
            SystemMessage(content=_SYSTEM),
            HumanMessage(content=str(messages)),
        ]
    result = model.invoke(lc_messages)
    if isinstance(result, PromptDraft):
        return result
    if isinstance(result, dict):
        return PromptDraft.model_validate(result)
    return PromptDraft.model_validate(result.model_dump())


def inventory_payload(
    evidence: ScanEvidence,
    nodes: tuple[Node, ...],
    *,
    preset: str,
    allowlist: Allowlist,
) -> dict:
    """Compact inventory the model is allowed to quote from."""
    return {
        "preset": preset,
        "services": [
            {
                "name": n.name,
                "kind": n.kind,
                "downstream": list(n.downstream),
                "log_services": list(n.log_services),
                "description": n.description,
            }
            for n in nodes
        ],
        "prometheus": {
            "url": evidence.prometheus.url,
            "service_label_values": dict(
                (k, list(v)[:40])
                for k, v in evidence.prometheus.label_values.items()
            ),
            "naming_markers": [
                {"metric": m.metric, "present": m.present, "means": m.means}
                for m in evidence.findings.naming_markers
                if m.present
            ],
            "alert_names": [r.name for r in evidence.prometheus.rules if r.name][:40],
        },
        "loki": {
            "url": evidence.loki.url,
            "service_label": evidence.loki.service_label,
            "level_field": evidence.loki.level_field,
            "stream_values": list(
                evidence.loki.label_values.get(evidence.loki.service_label or "service", ())
            )[:40],
            "alert_names": [r.name for r in evidence.loki.rules if r.name][:40],
        },
        "alertmanager": {
            "url": evidence.alertmanager.url,
            "receivers": list(evidence.alertmanager.receivers),
        },
        "allowlist_names": sorted(allowlist.names)[:80],
        "allowlist_ports": sorted(allowlist.ports),
    }


def author_prompt_profile(
    evidence: ScanEvidence,
    nodes: tuple[Node, ...],
    *,
    preset: str,
    extra_urls: tuple[str, ...] = (),
    invoke: InvokeFn | None = None,
    max_attempts: int = 2,
) -> DraftedFile:
    """Ask the model, validate, retry once, else withhold a stub."""
    allowlist = build_allowlist(
        evidence,
        node_names=tuple(n.name for n in nodes),
        extra_urls=extra_urls,
    )
    inventory = inventory_payload(evidence, nodes, preset=preset, allowlist=allowlist)
    invoke_fn = invoke or _default_invoke

    failures: list[str] = []
    last_draft: PromptDraft | None = None
    for attempt in range(1, max_attempts + 1):
        human = _human_prompt(inventory, failures)
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "human", "content": human},
        ]
        try:
            last_draft = _coerce(invoke_fn(messages))
        except Exception as exc:  # noqa: BLE001
            logger.warning("prompt_profile LLM call failed (attempt %s): %s", attempt, exc)
            failures = [f"model error: {exc}"]
            continue
        check = validate_prompt_profile(
            last_draft.platform_description,
            last_draft.tool_run_hints,
            allowlist,
        )
        if not check:
            return _accepted_file(last_draft, preset, attempt=attempt)
        failures = [str(f) for f in check]
        logger.info(
            "prompt_profile grounding failed attempt %s: %s", attempt, "; ".join(failures)
        )

    detail = "; ".join(failures) if failures else "model produced no usable draft"
    return _withheld_file(preset, detail, last_draft)


def _coerce(result: Any) -> PromptDraft:
    if isinstance(result, PromptDraft):
        return result
    if isinstance(result, dict):
        return PromptDraft.model_validate(result)
    if hasattr(result, "model_dump"):
        return PromptDraft.model_validate(result.model_dump())
    raise TypeError(f"unexpected prompt draft type: {type(result)}")


def _human_prompt(inventory: dict, failures: list[str]) -> str:
    body = (
        "Inventory (JSON — quote ONLY names/hosts/ports that appear here):\n"
        f"{json.dumps(inventory, indent=2, sort_keys=True)}\n"
    )
    if failures:
        body += (
            "\nPrevious attempt failed validation. Fix ALL of these before "
            "answering again:\n"
            + "\n".join(f"- {f}" for f in failures)
            + "\n"
        )
    return body


def _accepted_file(draft: PromptDraft, preset: str, *, attempt: int) -> DraftedFile:
    body = [
        f"extends: {preset}",
        "",
        "platform_description: >-",
        *[f"  {line}" for line in _fold(draft.platform_description)],
        "",
        "tool_run_hints: |-",
        *[f"  {line}" for line in draft.tool_run_hints.splitlines() or [""]],
        "",
    ]
    candidates = (
        Candidate(
            key="platform_description",
            value=draft.platform_description,
            why="LLM-authored from evidence inventory; grounding passed",
            verdict=VERIFIED,
            detail=f"accepted on attempt {attempt}",
        ),
        Candidate(
            key="tool_run_hints",
            value=draft.tool_run_hints[:120] + ("…" if len(draft.tool_run_hints) > 120 else ""),
            why="LLM-authored golden commands; grounding passed",
            verdict=VERIFIED,
            detail=f"accepted on attempt {attempt}",
        ),
    )
    header = render.header(
        "prompt_profile.yaml",
        purpose="Platform description and tool-run hints that frame the diagnostic LLM.",
        usage=(
            "Merged onto the preset; shapes tool_run_examples and fix_suggestions "
            "so the model stops inventing compose names and ports."
        ),
        evidence=[
            f"authored by LLM from the scan inventory (attempt {attempt})",
            "every quoted hostname, port, and service= filter passed grounding",
            "playbook: docs/PROMPT_PROFILE_AUTHORING.md",
        ],
        configure=(
            "Edit the allowlist and golden commands when your compose keys or "
            "host ports change. Keep names consistent with service_map.yaml."
        ),
    )
    return DraftedFile(
        path="prompt_profile.yaml",
        content=render.document(header, body),
        candidates=candidates,
    )


def _withheld_file(
    preset: str, detail: str, last_draft: PromptDraft | None
) -> DraftedFile:
    body = [
        f"extends: {preset}",
        "",
        f"# rejected: {detail}",
        "# platform_description and tool_run_hints were withheld because grounding",
        "# failed after retry. Re-run with --llm once the inventory is richer, or",
        "# author the file by hand using docs/PROMPT_PROFILE_AUTHORING.md.",
        "",
    ]
    if last_draft is not None:
        body.extend(
            [
                "# --- last model attempt (commented out) ---",
                "# platform_description: >-",
                *[f"#   {line}" for line in _fold(last_draft.platform_description)],
                "# tool_run_hints: |-",
                *[
                    f"#   {line}"
                    for line in (last_draft.tool_run_hints.splitlines() or [""])
                ],
                "",
            ]
        )
    candidates = (
        Candidate(
            key="platform_description",
            value=(last_draft.platform_description if last_draft else ""),
            why="LLM attempt failed grounding",
            verdict=REJECTED,
            detail=detail,
        ),
    )
    header = render.header(
        "prompt_profile.yaml",
        purpose="Platform description and tool-run hints that frame the diagnostic LLM.",
        usage="Merged onto the preset when present; this draft was withheld.",
        evidence=[f"withheld after grounding failure: {detail}"],
        configure="Author by hand or re-run diag draft --llm with a richer scan.",
        has_withheld=True,
    )
    return DraftedFile(
        path="prompt_profile.yaml",
        content=render.document(header, body),
        candidates=candidates,
    )


def _fold(text: str, width: int = 88) -> list[str]:
    """Soft-wrap a paragraph for a YAML ``>-`` block."""
    words = (text or "").split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}"
    lines.append(current)
    return lines
