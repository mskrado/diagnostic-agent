"""PromQL builders driven by the active integration profile.

Templates live in ``metrics_profile.yaml`` (or a built-in preset such as
``spring-micrometer`` / ``generic-prometheus``). Placeholders: ``{service}``,
``{window}``.
"""
from __future__ import annotations

from ..profile import get_profile


def _render(name: str, service: str, window: str = "5m") -> str:
    profile = get_profile().metrics
    out = profile.render(name, service=service, window=window)
    if out is None:
        raise KeyError(f"metrics profile has no template named {name!r}")
    return out


def error_rate(service: str, window: str = "5m") -> str:
    """Fraction of 5xx responses for a service over the window (0..1)."""
    return _render("error_rate", service, window)


def request_rate(service: str, window: str = "5m") -> str:
    return _render("request_rate", service, window)


def latency_p99(service: str, window: str = "5m") -> str:
    return _render("latency_p99", service, window)


def latency_p95(service: str, window: str = "5m") -> str:
    return _render("latency_p95", service, window)


def service_up(service: str) -> str:
    return _render("service_up", service)


def db_pool_pending(service: str) -> str:
    """Threads waiting on a DB pool connection — a pool-exhaustion signal."""
    return _render("db_pool_pending", service)


def db_pool_active(service: str) -> str:
    return _render("db_pool_active", service)


def jvm_heap_used_ratio(service: str) -> str:
    return _render("jvm_heap_used_ratio", service)


def dependency_probe(kind: str, service: str, window: str = "5m") -> str | None:
    """Return PromQL for a dependency kind, or None if the profile has no probe."""
    return get_profile().metrics.probe_for_kind(kind, service=service, window=window)


def service_kinds() -> tuple[str, ...]:
    return get_profile().metrics.service_kinds


def service_metric_names() -> tuple[str, ...]:
    return get_profile().metrics.service_metrics


def always_collect_names() -> tuple[str, ...]:
    return get_profile().metrics.always_collect


# Back-compat alias: older code imported DEPENDENCY_PROBES as a dict of callables.
# Prefer dependency_probe() for new code.
def _legacy_probe(kind: str):
    def _fn(svc: str, window: str = "5m") -> str | None:
        return dependency_probe(kind, svc, window)

    return _fn


# Populated lazily via __getattr__ so tests that poke DEPENDENCY_PROBES still work.
def __getattr__(name: str):
    if name == "DEPENDENCY_PROBES":
        probes = get_profile().metrics.dependency_probes
        return {kind: _legacy_probe(kind) for kind in probes}
    raise AttributeError(name)
