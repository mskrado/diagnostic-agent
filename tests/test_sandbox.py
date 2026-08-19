from __future__ import annotations

import subprocess
import types

import pytest

from app import config as config_mod
from app.execution.sandbox import ExecutionDisabled, Sandbox
from app.profile import reset_profile_cache


def _enable_exec(monkeypatch):
    monkeypatch.setenv("AGENT_EXEC_ENABLED", "true")
    config_mod.settings = config_mod.Settings()
    reset_profile_cache()


def _fake_completed(returncode=0, stdout="ok", stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_run_raises_when_exec_disabled(monkeypatch):
    monkeypatch.setenv("AGENT_EXEC_ENABLED", "false")
    config_mod.settings = config_mod.Settings()
    reset_profile_cache()
    sb = Sandbox()
    with pytest.raises(ExecutionDisabled):
        sb.run("clear-cdn-cache", {"service": "web-gateway"}, service="web-gateway")


def test_unknown_action_is_denied(monkeypatch):
    _enable_exec(monkeypatch)
    sb = Sandbox()
    res = sb.run("does-not-exist", {}, service="web-gateway")
    assert res.denied is True
    assert "unknown action" in (res.denial_reason or "")


def test_enum_param_outside_allowed_is_denied(monkeypatch):
    _enable_exec(monkeypatch)
    sb = Sandbox()
    res = sb.run("clear-cdn-cache", {"service": "web-gateway"}, service="not-in-scope")
    assert res.denied is True


def test_allowlisted_action_runs(monkeypatch):
    _enable_exec(monkeypatch)
    calls = {}

    def fake_run(cmd, capture_output, text, timeout):
        calls["cmd"] = cmd
        return _fake_completed(returncode=0, stdout="purged", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    sb = Sandbox()
    res = sb.run("clear-cdn-cache", {"service": "web-gateway"}, service="web-gateway")
    assert res.denied is False
    assert res.exit_code == 0
    assert res.stdout == "purged"
    assert "web-gateway" in res.argv
    assert all("{" not in tok for tok in res.argv)
    assert "--network" in calls["cmd"] and "none" in calls["cmd"]
    assert "--cap-drop" in calls["cmd"]


def test_timeout_returns_negative_exit(monkeypatch):
    _enable_exec(monkeypatch)

    def fake_run(cmd, capture_output, text, timeout):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    monkeypatch.setattr(subprocess, "run", fake_run)
    sb = Sandbox()
    res = sb.run("clear-cdn-cache", {"service": "web-gateway"}, service="web-gateway")
    assert res.exit_code < 0
