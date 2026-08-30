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

# Ordered: earlier patterns win on overlapping text. Each entry is
# (name, compiled pattern, replacement, what it is for).
_PATTERNS: tuple[tuple[str, re.Pattern[str], str, str], ...] = (
    (
        "private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
        "[PRIVATE-KEY-REDACTED]",
        "PEM private key block",
    ),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+"),
        "[JWT-REDACTED]",
        "JSON web token",
    ),
    (
        "bearer_token",
        re.compile(r"(bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE),
        r"\1[REDACTED]",
        "Authorization bearer token",
    ),
    (
        "aws_access_key",
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        "[AWS-KEY-REDACTED]",
        "AWS access key id",
    ),
    (
        "secret_kv",
        re.compile(
            r"(\"?(?:password|passwd|secret|token|api[_-]?key|authorization|"
            r"credential)\"?\s*[:=]\s*\"?)([^\"\s,;}]+)",
            re.IGNORECASE,
        ),
        r"\1[REDACTED]",
        "secret-looking key/value pair",
    ),
    (
        "email",
        re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
        "[EMAIL-REDACTED]",
        "email address",
    ),
    (
        "tenant_kv",
        re.compile(
            r"(\"?tenant[_-]?id\"?\s*[:=]\s*\"?)([^\"\s,;}]+)",
            re.IGNORECASE,
        ),
        r"\1[REDACTED]",
        "tenant identifier",
    ),
    (
        "uuid",
        re.compile(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
            re.IGNORECASE,
        ),
        "[UUID-REDACTED]",
        "UUID (often a tenant, user, or request id)",
    ),
    (
        "url_userinfo",
        re.compile(r"(?<=://)([^/\s:@]+):([^/\s@]+)@"),
        "[REDACTED]@",
        "credentials embedded in a URL",
    ),
    (
        "credit_card",
        re.compile(r"\b(?:\d[ -]?){13,16}\b"),
        "[CARD-REDACTED]",
        "card-length digit run",
    ),
)


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
    for _name, pattern, replacement, _desc in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def census(lines: list[str]) -> list[SecretHit]:
    """Count pattern hits across raw lines, before scrubbing.

    Sorted by line count so the report leads with whatever is most pervasive.
    """
    hits: list[SecretHit] = []
    for name, pattern, _replacement, description in _PATTERNS:
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
                    name=name,
                    description=description,
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
