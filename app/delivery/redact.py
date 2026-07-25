"""Configurable output redaction.

Rules come from ``redaction.yaml`` in the active integration profile (ordered
list of regex replacements). Hosts add tenant / PII patterns there; the
``generic-prometheus`` base preset supplies baseline secret scrubbing.
"""
from __future__ import annotations

import re
from functools import lru_cache

from ..profile import get_profile

_FLAG_MAP = {
    "IGNORECASE": re.IGNORECASE,
    "I": re.IGNORECASE,
    "MULTILINE": re.MULTILINE,
    "M": re.MULTILINE,
    "DOTALL": re.DOTALL,
    "S": re.DOTALL,
    "VERBOSE": re.VERBOSE,
    "X": re.VERBOSE,
}


def _compile_flags(flags: str) -> int:
    value = 0
    for part in (flags or "").replace("|", ",").split(","):
        token = part.strip().upper()
        if token in _FLAG_MAP:
            value |= _FLAG_MAP[token]
    return value


@lru_cache(maxsize=8)
def _compiled_rules(signature: str) -> tuple[tuple[re.Pattern[str], str], ...]:
    """Compile the active profile's rules. Keyed by signature so a profile
    change (or reset_profile_cache) yields a fresh compile."""
    compiled: list[tuple[re.Pattern[str], str]] = []
    for rule in get_profile().redaction.rules:
        try:
            compiled.append(
                (re.compile(rule.pattern, _compile_flags(rule.flags)), rule.replacement)
            )
        except re.error:
            continue
    return tuple(compiled)


def _signature() -> str:
    profile = get_profile()
    parts = [profile.name]
    for rule in profile.redaction.rules:
        parts.append(f"{rule.name}:{rule.pattern}:{rule.replacement}:{rule.flags}")
    return "|".join(parts)


def active_rule_names() -> tuple[str, ...]:
    """Names of the redaction rules in effect (for /health and startup checks)."""
    return tuple(rule.name for rule in get_profile().redaction.rules)


def redact_text(text: str) -> str:
    if not text:
        return text
    out = text
    for pattern, replacement in _compiled_rules(_signature()):
        out = pattern.sub(replacement, out)
    return out


def redact_log_lines(lines: list[str]) -> list[str]:
    return [redact_text(line) for line in lines]


def reset_redaction_cache() -> None:
    _compiled_rules.cache_clear()
