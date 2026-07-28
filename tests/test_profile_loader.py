"""Unit tests for integration-profile loading and presets."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.profile import build_profile, list_presets, reset_profile_cache
from app.profile.loader import load_preset

_ROOT = Path(__file__).resolve().parent.parent
_SPRING = _ROOT / "examples" / "spring-modular-monolith"


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
    # Presets carry conventions, not topology.
    assert profile.service_map_path is None


def test_partial_preset_still_inherits_base_redaction():
    """A preset that only defines metrics must not zero out redaction.

    Regression: spring-micrometer ships metrics only. Before the base-preset
    chain root, this resolved to 0 rules and silently disabled redaction.
    """
    for preset in list_presets():
        profile = build_profile(profile_dir=None, default_preset=preset)
        names = {r.name for r in profile.redaction.rules}
        assert "bearer_token" in names, f"preset {preset} lost base redaction"
        assert "aws_access_key" in names, f"preset {preset} lost base redaction"


def test_extends_appends_parent_redaction_rules(tmp_path):
    (tmp_path / "redaction.yaml").write_text(
        "extends: generic-prometheus\n"
        "rules:\n"
        "  - name: my_rule\n"
        "    pattern: 'secret-[0-9]+'\n"
        "    replacement: '[X]'\n",
        encoding="utf-8",
    )
    profile = build_profile(profile_dir=tmp_path, default_preset="generic-prometheus")
    names = [r.name for r in profile.redaction.rules]
    assert names == ["bearer_token", "aws_access_key", "my_rule"]


def test_extends_child_can_override_parent_rule_by_name(tmp_path):
    (tmp_path / "redaction.yaml").write_text(
        "extends: generic-prometheus\n"
        "rules:\n"
        "  - name: bearer_token\n"
        "    pattern: 'CUSTOM'\n"
        "    replacement: '[MINE]'\n",
        encoding="utf-8",
    )
    profile = build_profile(profile_dir=tmp_path, default_preset="generic-prometheus")
    by_name = {r.name: r for r in profile.redaction.rules}
    assert by_name["bearer_token"].pattern == "CUSTOM"
    assert "aws_access_key" in by_name


def test_spring_modular_monolith_uses_micrometer_and_tenant_redaction():
    assert _SPRING.is_dir(), "spring-modular-monolith example profile missing"
    profile = build_profile(
        profile_dir=_SPRING,
        default_preset="spring-micrometer",
        runbooks_override=str(_ROOT / "runbooks"),
    )
    assert profile.name == "spring-modular-monolith"
    q = profile.metrics.render("error_rate", service="platform-service", window="5m")
    assert q is not None
    assert "http_server_requests_seconds_count" in q
    assert "hikaricp_connections_pending" in (
        profile.metrics.templates.get("db_pool_pending") or ""
    )
    rule_names = {r.name for r in profile.redaction.rules}
    assert "tenant_token" in rule_names
    assert "platform-service" in profile.prompt.platform_description
    assert "publishi" not in profile.prompt.platform_description.lower()
    assert profile.logs.module_regex
    assert "SecurityAuthErrorsInLogs" in profile.logs.alert_line_filters
    assert profile.service_map_path and Path(profile.service_map_path).is_file()


def test_spring_modular_monolith_service_map_resolves_gateway():
    from app.dependency_map import DependencyMap

    profile = build_profile(profile_dir=_SPRING, default_preset="spring-micrometer")
    dep = DependencyMap.load(profile.service_map_path)
    assert "api-gateway" in dep.known_services()
    assert dep.kind("platform-service") == "monolith"


def test_unparseable_profile_yaml_recorded_as_load_error(tmp_path):
    """Broken overlay YAML must not silently fall back to preset-only config."""
    (tmp_path / "metrics_profile.yaml").write_text(
        "extends: spring-micrometer\n"
        "templates:\n"
        "  bad: [unterminated\n",
        encoding="utf-8",
    )
    (tmp_path / "redaction.yaml").write_text(
        "extends: generic-prometheus\n",
        encoding="utf-8",
    )
    profile = build_profile(profile_dir=tmp_path, default_preset="generic-prometheus")
    assert profile.load_errors
    assert any("metrics_profile.yaml" in e for e in profile.load_errors)
    # Redaction from the intact file still loads; the broken section is skipped.
    assert profile.redaction.rules

