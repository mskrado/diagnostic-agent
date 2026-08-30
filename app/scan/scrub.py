"""Workspace-independent scrubbing for sampled log lines.

``app.delivery.redact`` scrubs outbound reports using the *workspace's*
``redaction.yaml``. That is the wrong tool on its own here, for two reasons:

* a scan runs before a workspace exists — often in order to work out what the
  redaction rules should be, so it cannot depend on them;
* a host's rules may be narrow, and a scan holds raw log lines rather than the
  agent's own prose.

So this module carries its own fixed patterns, applied on top of the workspace
rules when those resolve. The same patterns produce a *census* — how many
sampled lines matched each pattern — which is both a report section and the
input to the redaction proposals in a later phase.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

_FLAGS = {
    "IGNORECASE": re.IGNORECASE,
    "DOTALL": re.DOTALL,
    "MULTILINE": re.MULTILINE,
}


@dataclass(frozen=True)
class PatternSpec:
    """A sensitive-data pattern, in the shape ``redaction.yaml`` uses.

    Sharing one definition means a rule proposed for a workspace is literally the
    rule the scan applied to the sample it was proposed from.
    """

    name: str
    pattern: str
    replacement: str
    description: str
    flags: str = ""
    # Whether to propose this as an *active* rule. Patterns with a real
    # false-positive cost on report prose are proposed commented out instead.
    propose_active: bool = True

    def compiled(self) -> re.Pattern[str]:
        value = 0
        for token in (self.flags or "").split(","):
            value |= _FLAGS.get(token.strip().upper(), 0)
        return re.compile(self.pattern, value)


# Ordered: earlier patterns win on overlapping text.
_SPECS: tuple[PatternSpec, ...] = (
    PatternSpec(
        "private_key",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        "[PRIVATE-KEY-REDACTED]",
        "PEM private key block",
        flags="DOTALL",
    ),
    PatternSpec(
        "jwt",
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+",
        "[JWT-REDACTED]",
        "JSON web token",
    ),
    PatternSpec(
        "bearer_token",
        r"(bearer\s+)[A-Za-z0-9._\-]+",
        r"\1[REDACTED]",
        "Authorization bearer token",
        flags="IGNORECASE",
    ),
    PatternSpec(
        "aws_access_key",
        r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
        "[AWS-KEY-REDACTED]",
        "AWS access key id",
    ),
    PatternSpec(
        "secret_kv",
        r"(\"?(?:password|passwd|secret|token|api[_-]?key|authorization|"
        r"credential)\"?\s*[:=]\s*\"?)([^\"\s,;}]+)",
        r"\1[REDACTED]",
        "secret-looking key/value pair",
        flags="IGNORECASE",
    ),
    PatternSpec(
        "email",
        r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
        "[EMAIL-REDACTED]",
        "email address",
    ),
    PatternSpec(
        "tenant_kv",
        r"(\"?tenant[_-]?id\"?\s*[:=]\s*\"?)([^\"\s,;}]+)",
        r"\1[REDACTED]",
        "tenant identifier",
        flags="IGNORECASE",
    ),
    PatternSpec(
        "uuid",
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        "[UUID-REDACTED]",
        "UUID (often a tenant, user, or request id)",
        flags="IGNORECASE",
        # Trace and request ids are UUIDs too, and they are how an operator
        # follows an incident across services.
        propose_active=False,
    ),
    PatternSpec(
        "url_userinfo",
        r"(?<=://)([^/\s:@]+):([^/\s@]+)@",
        "[REDACTED]@",
        "credentials embedded in a URL",
    ),
    PatternSpec(
        "credit_card",
        r"\b(?:\d[ -]?){13,16}\b",
        "[CARD-REDACTED]",
        "card-length digit run",
        # Matches any long digit run: byte counts, ids, epoch millis.
        propose_active=False,
    ),
)


def pattern_specs() -> tuple[PatternSpec, ...]:
    """The built-in patterns, for callers proposing ``redaction.yaml`` rules."""
    return _SPECS


@lru_cache(maxsize=1)
def _compiled() -> tuple[tuple[PatternSpec, re.Pattern[str]], ...]:
    return tuple((spec, spec.compiled()) for spec in _SPECS)


@dataclass(frozen=True)
class SecretHit:
    """One pattern's tally over a sample, for the report and later proposals."""

    name: str
    description: str
    lines: int
    matches: int

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "lines": self.lines,
            "matches": self.matches,
        }


def scrub_text(text: str) -> str:
    """Apply every built-in pattern to one string."""
    if not text:
        return text
    out = text
    for spec, pattern in _compiled():
        out = pattern.sub(spec.replacement, out)
    return out


def census(lines: list[str]) -> list[SecretHit]:
    """Count pattern hits across raw lines, before scrubbing.

    Sorted by line count so the report leads with whatever is most pervasive.
    """
    hits: list[SecretHit] = []
    for spec, pattern in _compiled():
        line_count = 0
        match_count = 0
        for line in lines:
            found = len(pattern.findall(line))
            if found:
                line_count += 1
                match_count += found
        if line_count:
            hits.append(
                SecretHit(
                    name=spec.name,
                    description=spec.description,
                    lines=line_count,
                    matches=match_count,
                )
            )
    hits.sort(key=lambda hit: (-hit.lines, hit.name))
    return hits


def workspace_scrubber():
    """Return the workspace's ``redact_text``, or identity when unavailable.

    A scan should work with no workspace at all, and should not fail because a
    profile is half-written — hence the broad guard.
    """
    try:
        from ..delivery.redact import redact_text

        redact_text("probe")
        return redact_text
    except Exception:  # noqa: BLE001 - any profile problem falls back to built-ins
        return lambda text: text


def scrub_lines(lines: list[str], *, use_workspace: bool = True) -> list[str]:
    """Scrub sampled lines with the workspace rules, then the built-ins."""
    profile_scrub = workspace_scrubber() if use_workspace else (lambda text: text)
    return [scrub_text(profile_scrub(line)) for line in lines]
