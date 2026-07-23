"""Build RAG queries that cover every distinct error family in a log sample.

A single similarity search over ``logs[:3]`` collapses mixed incidents onto
whatever family happens to appear first (usually postgres). Mirror how the
correlate prompt asks the LLM to inspect every line: classify the sample into
logical families, then retrieve per family and merge.
"""
from __future__ import annotations

# (family_label, keywords matched against lowercased log lines)
# Order is stable for tests; a line can match multiple families.
ERROR_FAMILIES: list[tuple[str, tuple[str, ...]]] = [
    (
        "database",
        (
            "postgres",
            "postgresql",
            "hikari",
            "jdbc",
            "psql",
            "datasource",
            "sqltransient",
            "sqlexception",
        ),
    ),
    ("cache", ("redis", "lettuce", "jedis")),
    (
        "search",
        (
            "elasticsearch",
            "circuit_breaking",
            "es_rejected",
            "index_not_found",
        ),
    ),
    (
        "jvm-memory",
        (
            "outofmemory",
            "out of memory",
            "java heap",
            "heap space",
            "gc pause",
            "metaspace",
            "high gc",
        ),
    ),
    (
        "gateway",
        (
            "readtimeoutexception",
            "no healthy upstream",
            "connection reset by peer",
            "bad_gateway",
            "nettyroutingfilter",
        ),
    ),
    (
        "auth",
        (
            "jwt",
            "csrf",
            "access denied",
            "authentication failed",
            "cross-tenant",
            "account locked",
            "cors",
        ),
    ),
    (
        "external-api",
        ("openai", "s3 ", "amazonaws", "smtp", "twilio", "nosuchbucket"),
    ),
    ("host", ("no space left", "disk space", "address already in use")),
]


def classify_log_families(log_lines: list[str]) -> dict[str, list[str]]:
    """Map family label → matching log lines (preserves input order)."""
    hits: dict[str, list[str]] = {}
    for line in log_lines or []:
        lower = line.lower()
        for family, keywords in ERROR_FAMILIES:
            if any(k in lower for k in keywords):
                hits.setdefault(family, []).append(line)
    return hits


def build_rag_queries(
    *,
    alert_type: str = "",
    service: str = "",
    module_hint: str = "",
    log_lines: list[str] | None = None,
) -> list[str]:
    """One retrieval query per distinct error family present in the sample.

    Falls back to a single alert+excerpt query when no family keywords match
    (unknown / sparse logs).
    """
    logs = list(log_lines or [])
    families = classify_log_families(logs)
    prefix = f"{alert_type} {service} {module_hint or ''}".strip()

    if not families:
        excerpt = " ".join(logs[:5])
        q = f"{prefix} {excerpt}".strip()
        return [q] if q else []

    queries: list[str] = []
    for family, matched in families.items():
        # Up to 2 representative lines so the embedding stays focused.
        excerpt = " ".join(matched[:2])
        queries.append(f"{prefix} {family} {excerpt}".strip())
    return queries
