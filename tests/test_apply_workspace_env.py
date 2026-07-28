"""Regression: empty AGENT_PROFILE_DIR must not shadow the mounted workspace."""
from __future__ import annotations

import os
from pathlib import Path

from app.cli import _apply_workspace_env
from app.workspace import Workspace


def test_empty_profile_dir_env_is_overridden_by_workspace(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("AGENT_PROFILE_DIR", "")
    monkeypatch.setenv("AGENT_RUNBOOKS_PATH", "   ")
    monkeypatch.delenv("AGENT_DEFAULT_PRESET", raising=False)

    profile = tmp_path / "workspace"
    runbooks = profile / "runbooks"
    runbooks.mkdir(parents=True)
    ws = Workspace(
        root=profile,
        manifest_path=None,
        preset="spring-micrometer",
        agent_version=None,
        profile_dir=profile,
        runbooks_dir=runbooks,
        scenarios_path=None,
        blind_eval_path=None,
    )
    _apply_workspace_env(ws)

    assert Path(os.environ["AGENT_PROFILE_DIR"]) == profile
    assert Path(os.environ["AGENT_RUNBOOKS_PATH"]) == runbooks
    assert os.environ["AGENT_DEFAULT_PRESET"] == "spring-micrometer"


def test_explicit_nonempty_profile_dir_env_wins(monkeypatch, tmp_path: Path):
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("AGENT_PROFILE_DIR", str(other))

    profile = tmp_path / "workspace"
    profile.mkdir()
    ws = Workspace(
        root=profile,
        manifest_path=None,
        preset="generic-prometheus",
        agent_version=None,
        profile_dir=profile,
        runbooks_dir=None,
        scenarios_path=None,
        blind_eval_path=None,
    )
    _apply_workspace_env(ws)
    assert Path(os.environ["AGENT_PROFILE_DIR"]) == other
