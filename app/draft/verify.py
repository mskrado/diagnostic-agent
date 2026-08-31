"""The oracle: cheap live checks that turn guesses into decisions.

Most of a workspace is a testable assertion. A metrics template is correct if
rendering it for a real service returns a vector; a log selector is correct if it
returns lines; a `module_regex` is correct if it captures a group on most real
log lines. That makes "write correct config for a stack I have never seen" a
search with a cheap oracle rather than an act of authorship.

Nothing here writes anything, and every check answers ``(ok, detail)`` so the
detail can be recorded next to whatever the check rejected.
"""
from __future__ import annotations

import logging
import re
from typing import Protocol

logger = logging.getLogger(__name__)


class Oracle(Protocol):
    """What the generators are allowed to ask about a live stack."""

    def promql(self, expr: str) -> tuple[bool, str]:
        """Does this query currently return any samples?"""

    def logql(self, selector: str) -> tuple[bool, str]:
        """Does this LogQL selector currently return any lines?"""

    def label_pairs(self, expr: str, *labels: str) -> tuple[tuple[str, ...], ...]:
        """Label tuples from a query's result vector (for topology edges)."""


class LiveOracle:
    """Oracle backed by the real Prometheus and Loki."""

    def __init__(
        self,
        prometheus_url: str,
        loki_url: str = "",
        *,
        timeout: float = 10.0,
        lookback_minutes: int = 60,
    ):
        from ..clients.loki import LokiClient
        from ..clients.prometheus import PrometheusClient

        self._prometheus = PrometheusClient(prometheus_url, timeout=timeout)
        self._loki = LokiClient(loki_url, timeout=timeout) if loki_url else None
        self._lookback = lookback_minutes
        self.queries = 0

    def promql(self, expr: str) -> tuple[bool, str]:
        if not expr:
            return False, "empty query"
        self.queries += 1
        result = self._prometheus.instant_raw(expr)
        if not result:
            return False, "query returned no data"
        return True, f"query returned {len(result)} series"

    def logql(self, selector: str) -> tuple[bool, str]:
        if self._loki is None:
            return False, "no Loki configured"
        if not selector:
            return False, "empty selector"
        self.queries += 1
        entries = self._loki.query_range(
            selector, lookback_minutes=self._lookback, limit=20
        )
        if not entries:
            return False, f"no lines in the last {self._lookback}m"
        return True, f"{len(entries)} line(s) in the last {self._lookback}m"

    def label_pairs(self, expr: str, *labels: str) -> tuple[tuple[str, ...], ...]:
        self.queries += 1
        pairs: list[tuple[str, ...]] = []
        for series in self._prometheus.instant_raw(expr):
            metric = series.get("metric")
            if not isinstance(metric, dict):
                continue
            values = tuple(str(metric.get(label) or "") for label in labels)
            if all(values):
                pairs.append(values)
        seen: dict[tuple[str, ...], None] = {}
        for pair in pairs:
            seen.setdefault(pair, None)
        return tuple(seen.keys())


class StubOracle:
    """Deterministic oracle for tests and for drafting without a stack.

    ``promql_ok`` / ``logql_ok`` accept a predicate or a collection of strings
    that should verify; anything else is rejected.
    """

    def __init__(self, promql_ok=(), logql_ok=(), pairs=None):
        self._promql_ok = promql_ok
        self._logql_ok = logql_ok
        self._pairs = pairs or {}
        self.asked: list[str] = []

    @staticmethod
    def _matches(rule, value: str) -> bool:
        if callable(rule):
            return bool(rule(value))
        return any(token in value for token in rule)

    def promql(self, expr: str) -> tuple[bool, str]:
        self.asked.append(expr)
        if self._matches(self._promql_ok, expr):
            return True, "query returned 1 series"
        return False, "query returned no data"

    def logql(self, selector: str) -> tuple[bool, str]:
        self.asked.append(selector)
        if self._matches(self._logql_ok, selector):
            return True, "3 line(s) in the last 60m"
        return False, "no lines in the last 60m"

    def label_pairs(self, expr: str, *labels: str) -> tuple[tuple[str, ...], ...]:
        self.asked.append(expr)
        return tuple(self._pairs.get(expr, ()))


# -- pure checks -------------------------------------------------------------
def captures_group(
    pattern: str, lines: tuple[str, ...] | list[str], *, min_ratio: float = 0.5
) -> tuple[bool, str]:
    """Does ``pattern`` capture a group on enough of these lines?

    A `module_regex` that matches nothing is worse than no regex at all: the
    agent would silently lose module attribution.
    """
    if not pattern:
        return False, "empty pattern"
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        return False, f"invalid regex: {exc}"
    if not lines:
        return False, "no sampled lines to check against"
    if not compiled.groups:
        return False, "regex has no capture group"

    hits = 0
    for line in lines:
        match = compiled.search(line)
        if match and match.group(1):
            hits += 1
    ratio = hits / len(lines)
    detail = f"captured on {hits}/{len(lines)} sampled line(s)"
    return ratio >= min_ratio, detail


def matches_lines(
    pattern: str, lines: tuple[str, ...] | list[str]
) -> tuple[bool, str]:
    """Does ``pattern`` match at least one sampled line?"""
    if not pattern:
        return False, "empty pattern"
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        return False, f"invalid regex: {exc}"
    if not lines:
        return False, "no sampled lines to check against"
    hits = sum(1 for line in lines if compiled.search(line))
    return hits > 0, f"matched {hits}/{len(lines)} sampled line(s)"
