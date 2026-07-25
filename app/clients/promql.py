"""PromQL access for the retrieve node.

All queries come from ``metrics_profile.yaml`` in the active integration profile
(or a built-in preset such as ``spring-micrometer`` / ``generic-prometheus``).
Placeholders: ``{service}``, ``{window}``.

The retrieve node renders profile metrics directly via
``get_profile().metrics.render(...)``; this module only exposes the dependency
probe lookup, which needs the kind → template indirection.
"""
from __future__ import annotations

from ..profile import get_profile


def render(name: str, service: str, window: str = "5m") -> str | None:
    """Render a named metric template, or None when the profile omits it."""
    return get_profile().metrics.render(name, service=service, window=window)


def dependency_probe(kind: str, service: str, window: str = "5m") -> str | None:
    """PromQL for a dependency kind, or None if the profile has no probe."""
    return get_profile().metrics.probe_for_kind(kind, service=service, window=window)
