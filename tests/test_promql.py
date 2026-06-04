from app.clients import promql


def test_error_rate_uses_micrometer_metric_and_service_label():
    q = promql.error_rate("platform-service", "5m")
    assert "http_server_requests_seconds_count" in q
    assert 'service="platform-service"' in q
    assert 'status=~"5.."' in q
    # must NOT use the generic reference-design metric name
    assert "http_requests_total" not in q


def test_latency_p99_is_histogram_quantile():
    q = promql.latency_p99("api-gateway")
    assert q.startswith("histogram_quantile(0.99")
    assert "http_server_requests_seconds_bucket" in q


def test_db_pool_pending_targets_hikaricp():
    assert "hikaricp_connections_pending" in promql.db_pool_pending("platform-service")
