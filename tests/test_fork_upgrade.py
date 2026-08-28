"""Tests for client-fork upgrade and drift detection."""
from __future__ import annotations

from pathlib import Path

from app.fork.boundary import is_upstream_owned
from app.fork.drift import read_upstream_version


def test_is_upstream_owned_client_paths():
    assert is_upstream_owned("client/README.md")
    assert not is_upstream_owned("client/workspace/agent.yaml")
    assert not is_upstream_owned("client/agent/docker-compose.yml")


def test_read_upstream_version_missing(tmp_path: Path):
    assert read_upstream_version(tmp_path) == ""


def test_read_upstream_version(tmp_path: Path):
    (tmp_path / ".upstream-version").write_text("1.2.3\n", encoding="utf-8")
    assert read_upstream_version(tmp_path) == "1.2.3"
