"""Hello-world example profile smoke test."""
from __future__ import annotations

from pathlib import Path

from app.dependency_map import DependencyMap
from app.profile import build_profile, reset_profile_cache

_ROOT = Path(__file__).resolve().parent.parent
_HELLO = _ROOT / "examples" / "hello-world"


def setup_function():
    reset_profile_cache()


def teardown_function():
    reset_profile_cache()


def test_hello_world_profile_loads():
    assert _HELLO.is_dir()
    profile = build_profile(
        profile_dir=_HELLO,
        default_preset="generic-prometheus",
    )
    assert profile.name == "hello-world"
    q = profile.metrics.render("error_rate", service="app", window="5m")
    assert "http_requests_total" in (q or "")
    assert (profile.root / "runbooks").is_dir()
    dep = DependencyMap.load(profile.service_map_path)
    assert "app" in dep.known_services()
    assert "postgres" in dep.blast_radius("app")
