import os

from app.dependency_map import DependencyMap

_MAP_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "examples",
    "spring-modular-monolith",
    "service_map.yaml",
)


def _load():
    return DependencyMap.load(_MAP_PATH)


def test_known_services_loaded():
    dm = _load()
    assert "platform-service" in dm.known_services()
    assert "api-gateway" in dm.known_services()


def test_blast_radius_of_platform_service_includes_backing_stores():
    dm = _load()
    blast = dm.blast_radius("platform-service")
    assert "postgres" in blast
    assert "redis" in blast


def test_resolve_strips_port_suffix():
    dm = _load()
    assert dm.resolve("platform-service:8080") == "platform-service"


def test_module_dependencies():
    dm = _load()
    assert "postgres" in dm.module_dependencies("auth")
    assert "elasticsearch" in dm.module_dependencies("search")


def test_log_services_for_logical_alert_targets():
    dm = _load()
    assert dm.log_services("security") == ["platform-service", "api-gateway"]
    assert dm.log_services("postgres") == ["platform-service"]
    assert dm.log_services("platform-service") == ["platform-service"]
    assert dm.log_selector("frontend") == '{app="frontend"}'
    assert dm.log_selector("security") is None
