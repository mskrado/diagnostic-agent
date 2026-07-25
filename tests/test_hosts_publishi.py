"""Monorepo host overlay — skipped when hosts/publishi is not present (public repo)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.dependency_map import DependencyMap
from app.profile import build_profile

_HOST = Path(__file__).resolve().parent.parent / "hosts" / "publishi"

pytestmark = pytest.mark.skipif(
    not _HOST.is_dir(),
    reason="hosts/publishi overlay is monorepo-only",
)


def test_publishi_host_overlay_faro_label():
    profile = build_profile(profile_dir=_HOST, default_preset="spring-micrometer")
    assert profile.name == "publishi"
    dep = DependencyMap.load(profile.service_map_path)
    assert dep.log_selector("frontend") == '{app="publishi-frontend"}'
    assert "publishi.ai" in profile.prompt.platform_description
