"""Tests for client-fork upgrade and drift detection."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.fork.boundary import is_upstream_owned
from app.fork.drift import DriftCheckError, find_upstream_drift, read_upstream_version
from app.fork.upgrade import run_upgrade


def test_is_upstream_owned_client_paths():
    assert is_upstream_owned("client/README.md")
    assert not is_upstream_owned("client/workspace/agent.yaml")
    assert not is_upstream_owned("client/agent/docker-compose.yml")


@pytest.mark.parametrize(
    "rel",
    [".github/workflows/ci.yml", ".gitignore", ".env.example", "app/cli.py", "README.md"],
)
def test_is_upstream_owned_matches_dotfiles(rel: str):
    """lstrip("./") ate the leading dot and hid every dotfile from drift checks."""
    assert is_upstream_owned(rel)


def test_read_upstream_version_missing(tmp_path: Path):
    assert read_upstream_version(tmp_path) == ""


def test_read_upstream_version(tmp_path: Path):
    (tmp_path / ".upstream-version").write_text("1.2.3\n", encoding="utf-8")
    assert read_upstream_version(tmp_path) == "1.2.3"


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


@pytest.fixture
def fork(tmp_path: Path) -> Path:
    """A minimal fork: upstream-owned app/ plus a client/ deployment dir."""
    repo = tmp_path / "fork"
    (repo / "app").mkdir(parents=True)
    (repo / "client").mkdir()
    (repo / "app" / "cli.py").write_text("print('v1')\n", encoding="utf-8")
    (repo / "client" / ".upstream-version").write_text("1.0.0\n", encoding="utf-8")
    _git(repo.parent, "init", "-q", "fork")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


def test_drift_detects_edits_to_upstream_paths(fork: Path):
    (fork / "app" / "cli.py").write_text("print('hacked')\n", encoding="utf-8")
    assert find_upstream_drift(fork) == ["app/cli.py"]


def test_drift_ignores_client_owned_changes(fork: Path):
    (fork / "client" / "agent").mkdir()
    (fork / "client" / "agent" / "docker-compose.yml").write_text("x\n", encoding="utf-8")
    assert find_upstream_drift(fork) == []


def test_drift_raises_outside_a_git_repo(tmp_path: Path):
    """A failed check must not read as 'clean' -- that would merge over edits."""
    with pytest.raises(DriftCheckError):
        find_upstream_drift(tmp_path)


def test_upgrade_refuses_when_upstream_paths_are_dirty(fork: Path, capsys):
    (fork / "app" / "cli.py").write_text("print('hacked')\n", encoding="utf-8")
    rc = run_upgrade(repo_root=fork, target="v1.1.0", client_dir=fork / "client")
    assert rc == 1
    err = capsys.readouterr().err
    assert "upstream-owned paths were modified locally" in err
    assert "app/cli.py" in err


def test_upgrade_reports_missing_remote_without_crashing(fork: Path, capsys):
    """This path used sys.stderr without importing sys, so it raised NameError."""
    rc = run_upgrade(repo_root=fork, remote="nope", client_dir=fork / "client")
    assert rc == 1
    assert "git remote 'nope' not found" in capsys.readouterr().err


def test_upgrade_merges_a_tag_and_bumps_the_marker(fork: Path, tmp_path: Path):
    """Offline-style upgrade: merge a local tag without touching the network."""
    _git(fork, "checkout", "-q", "-b", "release")
    (fork / "app" / "cli.py").write_text("print('v2')\n", encoding="utf-8")
    _git(fork, "commit", "-qam", "upstream v2")
    _git(fork, "tag", "v2.0.0")
    _git(fork, "checkout", "-q", "-")

    rc = run_upgrade(
        repo_root=fork,
        target="v2.0.0",
        client_dir=fork / "client",
        fetch_remote=False,
    )
    assert rc == 0
    assert (fork / "app" / "cli.py").read_text(encoding="utf-8") == "print('v2')\n"
    assert read_upstream_version(fork / "client") == "2.0.0"
