"""PromQL builders for publishi.ai.

IMPORTANT: publishi.ai exposes Spring Boot Micrometer metrics, NOT the generic
`http_requests_total` from the reference design. The relevant series are:

  - http_server_requests_seconds_count{service=..,status=..,uri=..}
  - http_server_requests_seconds_bucket{service=..,le=..}
  - hikaricp_connections_*            (DB pool, via HikariCP)
  - jvm_memory_used_bytes / jvm_memory_max_bytes
  - up{job=..}

The `service` label is attached by Micrometer (`management.metrics.tags.application`)
and Prometheus relabeling (`service: api-gateway` / `service: platform-service`).
"""
from __future__ import annotations


def error_rate(service: str, window: str = "5m") -> str:
    """Fraction of 5xx responses for a service over the window (0..1)."""
    return (
        f'sum(rate(http_server_requests_seconds_count{{service="{service}",status=~"5.."}}[{window}]))'
        f' / clamp_min(sum(rate(http_server_requests_seconds_count{{service="{service}"}}[{window}])), 0.001)'
    )


def request_rate(service: str, window: str = "5m") -> str:
    return f'sum(rate(http_server_requests_seconds_count{{service="{service}"}}[{window}]))'


def latency_p99(service: str, window: str = "5m") -> str:
    return (
        "histogram_quantile(0.99, sum by (le) ("
        f'rate(http_server_requests_seconds_bucket{{service="{service}"}}[{window}])))'
    )


def latency_p95(service: str, window: str = "5m") -> str:
    return (
        "histogram_quantile(0.95, sum by (le) ("
        f'rate(http_server_requests_seconds_bucket{{service="{service}"}}[{window}])))'
    )


def service_up(service: str) -> str:
    return f'up{{service="{service}"}}'


def db_pool_pending(service: str) -> str:
    """Threads waiting on a HikariCP connection -- a pool-exhaustion signal."""
    return f'hikaricp_connections_pending{{service="{service}"}}'


def db_pool_active(service: str) -> str:
    return f'hikaricp_connections_active{{service="{service}"}}'


def jvm_heap_used_ratio(service: str) -> str:
    return (
        f'sum(jvm_memory_used_bytes{{service="{service}",area="heap"}})'
        f' / clamp_min(sum(jvm_memory_max_bytes{{service="{service}",area="heap"}}), 1)'
    )


# Dependency-specific probes keyed by the `kind` field in service_map.yaml.
# Each returns a PromQL string given the owning service. Missing exporters
# simply yield empty results (handled gracefully downstream).
DEPENDENCY_PROBES: dict[str, callable] = {
    "database": db_pool_pending,
    "redis": lambda svc: f'lettuce_command_completion_seconds_count{{service="{svc}"}}',
}
