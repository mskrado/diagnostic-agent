"""Workspace manifest resolution — the contract host projects code against."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.tools.corpus_lint import lint
from app.workspace import SCHEMA_VERSION, WorkspaceError
from app.workspace import load as load_workspace

_ROOT = Path(__file__).resolve().parent.parent
_EXAMPLES = _ROOT / "examples"


@pytest.fixture(autouse=True)
def _no_inherited_preset(monkeypatch):
    """conftest pins a preset for profile tests; workspace resolution is its own."""
    monkeypatch.delenv("AGENT_DEFAULT_PRESET", raising=False)
    monkeypatch.delenv("AGENT_WORKSPACE", raising=False)


def _write(ws: Path, rel: str, text: str = "{}") -> Path:
    path = ws / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# this repository and the bundled examples resolve as workspaces
# --------------------------------------------------------------------------
def test_repo_root_resolves_through_conventional_layout():
    ws = load_workspace(_ROOT)
    assert ws.manifest_path is None
    assert ws.runbooks_dir == _ROOT / "runbooks"
    assert ws.scenarios_path == _ROOT / "runbook_scenarios.yaml"
    assert ws.blind_eval_path == _ROOT / "eval" / "blind_eval_dataset.yaml"


def test_example_manifest_supplies_preset():
    ws = load_workspace(_EXAMPLES / "spring-modular-monolith")
    assert ws.preset == "spring-micrometer"
    assert ws.manifest_path is not None


def test_flat_profile_directory_is_the_profile():
    """Sections at the workspace root need no `profile:` key."""
    ws = load_workspace(_EXAMPLES / "hello-world")
    assert ws.profile_dir == _EXAMPLES / "hello-world"
    assert ws.runbooks_dir == _EXAMPLES / "hello-world" / "runbooks"


def test_example_profiles_carry_redaction_rules():
    for name in ("hello-world", "spring-modular-monolith"):
        profile = load_workspace(_EXAMPLES / name).profile()
        assert profile.redaction.rules, f"{name} resolved zero redaction rules"


# --------------------------------------------------------------------------
# manifest handling
# --------------------------------------------------------------------------
def test_declared_paths_win_over_convention(tmp_path):
    _write(tmp_path, "conf/redaction.yaml", "rules: []")
    _write(tmp_path, "books/runbook-x.md", "# x")
    _write(tmp_path, "cases.yaml", "cases: []")
    _write(
        tmp_path,
        "agent.yaml",
        "schema: 1\nprofile: ./conf\nrunbooks: ./books\nblind_eval: ./cases.yaml\n",
    )

    ws = load_workspace(tmp_path)
    assert ws.profile_dir == tmp_path / "conf"
    assert ws.runbooks_dir == tmp_path / "books"
    assert ws.blind_eval_path == tmp_path / "cases.yaml"


def test_missing_declared_path_is_an_error(tmp_path):
    _write(tmp_path, "agent.yaml", "schema: 1\nprofile: ./nope\n")
    with pytest.raises(WorkspaceError, match="not an existing directory"):
        load_workspace(tmp_path)


def test_newer_schema_is_refused(tmp_path):
    _write(tmp_path, "agent.yaml", f"schema: {SCHEMA_VERSION + 1}\n")
    with pytest.raises(WorkspaceError, match="newer than this agent supports"):
        load_workspace(tmp_path)


def test_unknown_manifest_keys_warn_but_load(tmp_path):
    _write(tmp_path, "agent.yaml", "schema: 1\nrunbook_dir: ./typo\n")
    ws = load_workspace(tmp_path)
    assert any("runbook_dir" in w for w in ws.warnings)


def test_missing_manifest_can_be_required(tmp_path):
    with pytest.raises(WorkspaceError, match="no agent.yaml"):
        load_workspace(tmp_path, require_manifest=True)


def test_env_preset_overrides_manifest(tmp_path, monkeypatch):
    _write(tmp_path, "agent.yaml", "schema: 1\nextends: spring-micrometer\n")
    monkeypatch.setenv("AGENT_DEFAULT_PRESET", "generic-prometheus")
    assert load_workspace(tmp_path).preset == "generic-prometheus"


def test_env_var_locates_the_workspace(tmp_path, monkeypatch):
    _write(tmp_path, "agent.yaml", "schema: 1\n")
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    assert load_workspace().root == tmp_path.resolve()


# --------------------------------------------------------------------------
# tools degrade gracefully on a partial workspace
# --------------------------------------------------------------------------
def test_lint_skips_checks_a_workspace_cannot_supply(tmp_path):
    _write(tmp_path, "agent.yaml", "schema: 1\n")
    result = lint(load_workspace(tmp_path))
    assert result.ok
    assert len(result.notes) == 3


def test_lint_flags_a_runbook_missing_the_hypotheses_section(tmp_path):
    _write(tmp_path, "agent.yaml", "schema: 1\n")
    _write(tmp_path, "runbooks/runbook-bad.md", "# No disclaimer here")
    result = lint(load_workspace(tmp_path))
    assert not result.ok
    assert any("Hypotheses-only" in e for e in result.errors)
