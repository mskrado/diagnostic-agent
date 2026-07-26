"""Host workspace — the contract between a host project and the agent.

A workspace is a directory in the *host* repository holding everything specific
to that host: the integration profile, the runbook corpus, and the test/eval
fixtures. An ``agent.yaml`` manifest declares where each piece lives::

    <workspace>/
    ├── agent.yaml
    ├── profile/         # metrics, logs, redaction, prompt, service_map
    ├── runbooks/        # RAG corpus
    ├── scenarios.yaml   # runbook E2E scenarios
    └── blind_eval.yaml  # blind-eval dataset

Every ``diag`` subcommand resolves its inputs from the manifest, so host
projects never pass paths on the command line::

    docker run --rm -v "$PWD/infrastructure/diagnostic-agent:/workspace" \\
        ghcr.io/mskrado/diagnostic-agent:<tag> diag validate

The manifest is optional. A directory following the conventional layout
resolves identically, which keeps this repository's own root a valid workspace
so the agent runs its tooling against itself in CI.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

MANIFEST_NAME = "agent.yaml"
ENV_VAR = "AGENT_WORKSPACE"

# Bumped only for breaking manifest changes. The agent refuses a workspace
# written against a newer schema rather than silently misreading it.
SCHEMA_VERSION = 1

_MANIFEST_KEYS = {
    "schema",
    "agent_version",
    "extends",
    "profile",
    "runbooks",
    "scenarios",
    "blind_eval",
}

# Any of these at the top of a directory marks it as an integration profile.
_PROFILE_MARKERS = (
    "metrics_profile.yaml",
    "logs_profile.yaml",
    "redaction.yaml",
    "prompt_profile.yaml",
    "service_map.yaml",
)

_DEFAULT_PRESET = "generic-prometheus"

# Conventional locations, tried in order when the manifest omits a key. The
# second entry of each pair is this repository's own historical layout.
_PROFILE_DEFAULTS = ("profile",)
_RUNBOOK_DEFAULTS = ("runbooks",)
_SCENARIO_DEFAULTS = ("scenarios.yaml", "runbook_scenarios.yaml")
_BLIND_EVAL_DEFAULTS = ("blind_eval.yaml", "eval/blind_eval_dataset.yaml")


class WorkspaceError(RuntimeError):
    """The manifest is malformed, or a path it declares does not exist."""


@dataclass(frozen=True)
class Workspace:
    """Resolved host workspace. Build with :func:`load`."""

    root: Path
    manifest_path: Path | None
    agent_version: str | None
    preset: str
    profile_dir: Path | None
    runbooks_dir: Path | None
    scenarios_path: Path | None
    blind_eval_path: Path | None
    warnings: tuple[str, ...] = ()

    def profile(self):
        """Build the IntegrationProfile this workspace configures."""
        from .profile import build_profile

        return build_profile(
            profile_dir=self.profile_dir,
            default_preset=self.preset,
            runbooks_override=str(self.runbooks_dir) if self.runbooks_dir else None,
        )

    def describe(self) -> dict[str, str]:
        """Flat path summary for ``diag validate`` and ``/health``."""
        return {
            "root": str(self.root),
            "manifest": str(self.manifest_path) if self.manifest_path else "(none)",
            "preset": self.preset,
            "profile": str(self.profile_dir) if self.profile_dir else "(none)",
            "runbooks": str(self.runbooks_dir) if self.runbooks_dir else "(none)",
            "scenarios": str(self.scenarios_path) if self.scenarios_path else "(none)",
            "blind_eval": (
                str(self.blind_eval_path) if self.blind_eval_path else "(none)"
            ),
        }


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise WorkspaceError(f"{path}: cannot read manifest: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkspaceError(f"{path}: manifest must be a YAML mapping")
    return data


def _check_schema(manifest: dict[str, Any], path: Path) -> list[str]:
    warnings: list[str] = []
    schema = manifest.get("schema", SCHEMA_VERSION)
    if not isinstance(schema, int):
        raise WorkspaceError(f"{path}: schema must be an integer, got {schema!r}")
    if schema > SCHEMA_VERSION:
        raise WorkspaceError(
            f"{path}: manifest schema {schema} is newer than this agent supports "
            f"({SCHEMA_VERSION}). Upgrade the agent image."
        )
    unknown = sorted(set(manifest) - _MANIFEST_KEYS)
    if unknown:
        warnings.append(f"unknown manifest keys ignored: {', '.join(unknown)}")
    return warnings


def _declared(
    root: Path, manifest: dict[str, Any], key: str, *, want_dir: bool
) -> Path | None:
    """Resolve an explicit manifest path, failing loudly when it is missing."""
    value = manifest.get(key)
    if value in (None, ""):
        return None
    path = (root / str(value)).resolve()
    ok = path.is_dir() if want_dir else path.is_file()
    if not ok:
        kind = "directory" if want_dir else "file"
        raise WorkspaceError(
            f"{root / MANIFEST_NAME}: {key}: {value!r} is not an existing {kind} "
            f"(resolved to {path})"
        )
    return path


def _conventional(root: Path, candidates: tuple[str, ...], *, want_dir: bool):
    for rel in candidates:
        path = (root / rel).resolve()
        if path.is_dir() if want_dir else path.is_file():
            return path
    return None


def _resolve_profile_dir(root: Path, manifest: dict[str, Any]) -> Path | None:
    declared = _declared(root, manifest, "profile", want_dir=True)
    if declared is not None:
        return declared
    conventional = _conventional(root, _PROFILE_DEFAULTS, want_dir=True)
    if conventional is not None:
        return conventional
    # Profile sections sitting directly in the workspace root (the layout used
    # by examples/ and by hosts that keep a single flat directory).
    if any((root / marker).is_file() for marker in _PROFILE_MARKERS):
        return root
    return None


def discover(start: str | Path | None = None) -> Path:
    """Locate the workspace root.

    Explicit argument, then ``AGENT_WORKSPACE``, then the nearest ancestor of
    the working directory containing a manifest, then the working directory.
    """
    if start:
        return Path(start).expanduser().resolve()
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env).expanduser().resolve()
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / MANIFEST_NAME).is_file():
            return candidate
    return cwd


def load(path: str | Path | None = None, *, require_manifest: bool = False) -> Workspace:
    """Resolve a :class:`Workspace` from ``path`` (see :func:`discover`)."""
    root = discover(path)
    if not root.is_dir():
        raise WorkspaceError(f"workspace {root} is not a directory")

    manifest_path = root / MANIFEST_NAME
    if manifest_path.is_file():
        manifest = _read_manifest(manifest_path)
        warnings = _check_schema(manifest, manifest_path)
    elif require_manifest:
        raise WorkspaceError(f"no {MANIFEST_NAME} in {root}. Run `diag init` first.")
    else:
        manifest, manifest_path, warnings = {}, None, []

    profile_dir = _resolve_profile_dir(root, manifest)

    runbooks_dir = _declared(root, manifest, "runbooks", want_dir=True) or _conventional(
        root, _RUNBOOK_DEFAULTS, want_dir=True
    )
    # A flat profile directory may carry its own runbooks/ subtree.
    if runbooks_dir is None and profile_dir is not None:
        candidate = profile_dir / "runbooks"
        runbooks_dir = candidate if candidate.is_dir() else None

    scenarios_path = _declared(
        root, manifest, "scenarios", want_dir=False
    ) or _conventional(root, _SCENARIO_DEFAULTS, want_dir=False)

    blind_eval_path = _declared(
        root, manifest, "blind_eval", want_dir=False
    ) or _conventional(root, _BLIND_EVAL_DEFAULTS, want_dir=False)

    # Env wins so a container can retarget the preset without editing the host
    # manifest, matching the precedence used by Settings.
    preset = (
        os.environ.get("AGENT_DEFAULT_PRESET")
        or manifest.get("extends")
        or _DEFAULT_PRESET
    )

    agent_version = manifest.get("agent_version")
    workspace = Workspace(
        root=root,
        manifest_path=manifest_path,
        agent_version=str(agent_version) if agent_version else None,
        preset=str(preset),
        profile_dir=profile_dir,
        runbooks_dir=runbooks_dir,
        scenarios_path=scenarios_path,
        blind_eval_path=blind_eval_path,
        warnings=tuple(warnings),
    )
    logger.debug("Resolved workspace %s", workspace.describe())
    return workspace
