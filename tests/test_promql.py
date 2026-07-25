from app.clients import promql


def test_error_rate_uses_micrometer_metric_and_service_label():
    q = promql.render("error_rate", "platform-service", "5m")
    assert "http_server_requests_seconds_count" in q
    assert 'service="platform-service"' in q
    assert 'status=~"5.."' in q
    # must NOT use the generic reference-design metric name
    assert "http_requests_total" not in q


def test_latency_p99_is_histogram_quantile():
    q = promql.render("latency_p99", "api-gateway")
    assert q.startswith("histogram_quantile(0.99")
    assert "http_server_requests_seconds_bucket" in q


def test_db_pool_pending_targets_hikaricp():
    assert "hikaricp_connections_pending" in promql.render(
        "db_pool_pending", "platform-service"
    )


def test_render_returns_none_for_unknown_metric():
    assert promql.render("no_such_metric", "platform-service") is None


def test_dependency_probe_resolves_kind_via_template_name():
    # spring-micrometer maps kind "database" -> the db_pool_pending template.
    assert "hikaricp_connections_pending" in promql.dependency_probe(
        "database", "platform-service"
    )


def test_dependency_probe_resolves_inline_promql():
    q = promql.dependency_probe("redis", "platform-service")
    assert 'service="platform-service"' in q


def test_dependency_probe_returns_none_for_unmapped_kind():
    assert promql.dependency_probe("not-a-kind", "postgres") is None
