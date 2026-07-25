"""The startup guard that refuses to run without redaction rules.

Regression: a mis-mounted AGENT_PROFILE_DIR (Docker turns a missing bind-mount
source into an empty directory) combined with a metrics-only preset resolved to
zero redaction rules, so reports shipped raw tenant identifiers.
"""
from __future__ import annotations

import pytest

from app import config as config_mod
from app.profile import reset_profile_cache


@pytest.fixture
def empty_profile(tmp_path, monkeypatch):
    """Point the agent at an empty profile dir with no redaction rules anywhere."""
    monkeypatch.setenv("AGENT_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_DEFAULT_PRESET", "no-such-preset")
    config_mod.settings = config_mod.Settings()
    reset_profile_cache()
    from app.delivery.redact import reset_redaction_cache

    reset_redaction_cache()
    yield tmp_path


def test_unknown_preset_still_inherits_base_redaction(empty_profile):
    """An unknown preset falls back to the base preset rather than to nothing."""
    from app.delivery.redact import active_rule_names, redact_text

    assert set(active_rule_names()) == {"bearer_token", "aws_access_key"}
    assert "AKIA" not in redact_text("key AKIAIOSFODNN7EXAMPLE here")


def test_guard_raises_when_redaction_resolves_empty(monkeypatch, empty_profile):
    from app import agent as agent_mod

    monkeypatch.setattr(agent_mod, "active_rule_names", lambda: ())
    with pytest.raises(RuntimeError, match="0 redaction rules"):
        agent_mod._check_redaction()


def test_guard_can_be_disabled_explicitly(monkeypatch, empty_profile, caplog):
    from app import agent as agent_mod

    monkeypatch.setattr(agent_mod, "active_rule_names", lambda: ())
    monkeypatch.setattr(agent_mod.settings, "require_redaction", False)
    with caplog.at_level("ERROR"):
        assert agent_mod._check_redaction() == ()
    assert "0 redaction rules" in caplog.text


def test_guard_passes_with_publishi_profile():
    """Default (conftest-pinned) profile satisfies the guard."""
    from app.agent import _check_redaction

    assert "tenant_token" in _check_redaction()
