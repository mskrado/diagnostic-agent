"""Configurable output redaction.

Rules come from ``redaction.yaml`` in the active integration profile (ordered
list of regex replacements). Hosts add tenant / PII patterns there.
"""
from __future__ import annotations

import re
from functools import lru_cache

from ..profile import get_profile
from ..profile.models import RedactionRule

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
        if not token:
            continue
        if token not in _FLAG_MAP:
            continue
        value |= _FLAG_MAP[token]
    return value


@lru_cache(maxsize=8)
def _compiled_rules(profile_key: str) -> tuple[tuple[re.Pattern[str], str], ...]:
    del profile_key  # key is only for cache identity
    compiled: list[tuple[re.Pattern[str], str]] = []
    for rule in get_profile().redaction.rules:
        try:
            compiled.append(
                (
                    re.compile(rule.pattern, _compile_flags(rule.flags)),
                    rule.replacement,
                )
            )
        except re.error:
            continue
    return tuple(compiled)


def _cache_key() -> str:
    profile = get_profile()
    # Identity from rule signatures so reset_profile_cache + new rules refresh.
    parts = [profile.name]
    for rule in profile.redaction.rules:
        parts.append(f"{rule.name}:{rule.pattern}:{rule.replacement}:{rule.flags}")
    return "|".join(parts)


def redact_text(text: str) -> str:
    if not text:
        return text
    out = text
    for pattern, replacement in _compiled_rules(_cache_key()):
        out = pattern.sub(replacement, out)
    return out


def redact_log_lines(lines: list[str]) -> list[str]:
    return [redact_text(line) for line in lines]


def reset_redaction_cache() -> None:
    _compiled_rules.cache_clear()


def rules_from_profile() -> tuple[RedactionRule, ...]:
    return get_profile().redaction.rules
