"""Unit tests for integration-profile loading and presets."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.profile import build_profile, list_presets, reset_profile_cache
from app.profile.loader import load_preset

_ROOT = Path(__file__).resolve().parent.parent
_PUBLISHI = _ROOT / "integrations" / "publishi"


@pytest.fixture(autouse=True)
def _clear_profile_cache():
    reset_profile_cache()
    yield
    reset_profile_cache()


def test_list_presets_includes_builtins():
    names = list_presets()
    assert "generic-prometheus" in names
    assert "spring-micrometer" in names


def test_spring_micrometer_preset_has_hikaricp():
    data = load_preset("spring-micrometer")
    templates = (data.get("metrics") or {}).get("templates") or {}
    # templates may also be top-level metric keys after from_dict; check raw YAML shape
    assert "db_pool_pending" in templates or "hikaricp" in str(data)


def test_generic_prometheus_profile_smoke():
    profile = build_profile(
        profile_dir=None,
        default_preset="generic-prometheus",
    )
    assert profile.name == "generic-prometheus"
    q = profile.metrics.render("error_rate", service="api", window="5m")
    assert q is not None
    assert "http_requests_total" in q
    assert "http_server_requests_seconds_count" not in q


def test_publishi_profile_uses_micrometer_and_tenant_redaction():
    assert _PUBLISHI.is_dir(), "publishi integration profile missing"
    profile = build_profile(
        profile_dir=_PUBLISHI,
        default_preset="spring-micrometer",
        runbooks_override=str(_ROOT / "runbooks"),
    )
    assert profile.name == "publishi"
    q = profile.metrics.render("error_rate", service="platform-service", window="5m")
    assert q is not None
    assert "http_server_requests_seconds_count" in q
    assert "hikaricp_connections_pending" in (
        profile.metrics.templates.get("db_pool_pending") or ""
    )
    rule_names = {r.name for r in profile.redaction.rules}
    assert "tenant_token" in rule_names
    assert "platform-service" in profile.prompt.platform_description
    assert profile.logs.module_regex
    assert "SecurityAuthErrorsInLogs" in profile.logs.alert_line_filters
    assert profile.service_map_path and Path(profile.service_map_path).is_file()


def test_publishi_profile_service_map_resolves_gateway():
    from app.dependency_map import DependencyMap

    profile = build_profile(profile_dir=_PUBLISHI, default_preset="spring-micrometer")
    dep = DependencyMap.load(profile.service_map_path)
    assert "api-gateway" in dep.known_services()
    assert dep.kind("platform-service") == "monolith"
