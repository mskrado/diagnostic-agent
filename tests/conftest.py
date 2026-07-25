"""Pytest defaults — pin the publishi integration profile for regression tests.

Individual tests may override via ``build_profile`` / env + ``reset_profile_cache``.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_PUBLISHI = _ROOT / "integrations" / "publishi"


@pytest.fixture(autouse=True)
def _pin_publishi_profile(monkeypatch):
    monkeypatch.setenv("AGENT_PROFILE_DIR", str(_PUBLISHI))
    monkeypatch.setenv("AGENT_DEFAULT_PRESET", "spring-micrometer")
    # Clear any empty overrides so profile paths resolve from the profile dir.
    monkeypatch.delenv("AGENT_SERVICE_MAP_PATH", raising=False)
    monkeypatch.delenv("AGENT_RUNBOOKS_PATH", raising=False)

    # Settings + profile caches may already have been constructed at import time.
    from app import config as config_mod
    from app.delivery.redact import reset_redaction_cache
    from app.profile import reset_profile_cache

    config_mod.settings = config_mod.Settings()
    reset_profile_cache()
    reset_redaction_cache()
    yield
    reset_profile_cache()
    reset_redaction_cache()
