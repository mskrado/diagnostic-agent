"""Alert-name → runbook index for pinning whole runbooks into RAG context.

Similarity search over fixed-size chunks can return only a runbook's intro when
an alert carries no logs (host/disk alerts), dropping its checks and causes. A
runbook that names the firing alert in its ``**Alert:**`` / ``**Alerts:**``
header is included whole instead.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

_HEADER_RE = re.compile(r"^\*\*Alerts?:?\*\*:?", re.IGNORECASE)
# CamelCase identifiers only: skips PromQL, LogQL, service names and regexes
# that also appear in backticks in runbook headers.
_ALERT_NAME_RE = re.compile(r"`([A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)+)`")


def parse_alert_names(text: str) -> set[str]:
    """Alert names from the ``**Alert(s):**`` header block (until a blank line/heading)."""
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        if not _HEADER_RE.match(line.strip()):
            continue
        block = [line]
        for nxt in lines[i + 1 :]:
            stripped = nxt.strip()
            if not stripped or stripped.startswith("#"):
                break
            block.append(nxt)
        return set(_ALERT_NAME_RE.findall("\n".join(block)))
    return set()


def build_alert_index(docs: Iterable[str]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for text in docs:
        body = (text or "").strip()
        if not body:
            continue
        for name in sorted(parse_alert_names(body)):
            index.setdefault(name, []).append(body)
    return index


def pinned_docs(
    index: dict[str, list[str]],
    alert_type: str | None,
    *,
    max_docs: int,
    max_chars: int,
) -> list[str]:
    """Whole runbooks for ``alert_type``; empty when the alert is too generic.

    An alert named by more than ``max_docs`` runbooks (e.g. a catch-all
    external-dependency alert) is left to similarity search.
    """
    docs = index.get((alert_type or "").strip()) or []
    if not docs or len(docs) > max_docs:
        return []
    out: list[str] = []
    budget = max_chars
    for doc in docs:
        if budget <= 0:
            break
        if len(doc) > budget:
            doc = doc[:budget].rstrip() + "\n…(truncated)"
        out.append(doc)
        budget -= len(doc)
    return out
