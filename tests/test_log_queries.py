"""Tests for alert-aware Loki LogQL construction."""
from __future__ import annotations

from app.log_queries import build_retrieve_logql, stream_selector


def test_stream_selector_single_service():
    assert stream_selector(service="platform-service") == '{service="platform-service"}'


def test_stream_selector_log_services_regex():
    assert (
        stream_selector(
            service="security",
            log_services=["platform-service", "api-gateway"],
        )
        == '{service=~"platform-service|api-gateway"}'
    )


def test_stream_selector_override():
    assert (
        stream_selector(
            service="frontend",
            log_selector='{app="publishi-frontend"}',
        )
        == '{app="publishi-frontend"}'
    )


def test_security_alert_uses_ruler_line_filter_not_level():
    logql, meta = build_retrieve_logql(
        service="security",
        alert_type="SecurityAuthErrorsInLogs",
        log_services=["platform-service", "api-gateway"],
    )
    assert logql.startswith('{service=~"platform-service|api-gateway"}')
    assert "|~" in logql
    assert "jwt" in logql
    assert "level=" not in logql
    assert meta["level"] is None
    assert "platform-service" in meta["log_services"]


def test_generic_alert_keeps_error_warn_level():
    logql, meta = build_retrieve_logql(
        service="platform-service",
        alert_type="HighErrorRate",
    )
    assert logql == '{service="platform-service"} | json | level=~"ERROR|WARN"'
    assert meta["level"] == "ERROR|WARN"


def test_postgres_alert_maps_to_platform_service_with_filter():
    logql, meta = build_retrieve_logql(
        service="postgres",
        alert_type="PostgresErrorsInLogs",
        log_services=["platform-service"],
    )
    assert '{service="platform-service"}' in logql
    assert "hikari" in logql.lower() or "postgres" in logql.lower()
    assert meta["level"] is None
