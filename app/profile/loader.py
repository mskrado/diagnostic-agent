"""Load and merge an integration profile from disk + built-in presets.

Config precedence (highest wins):
  1. Environment / Settings path overrides (service_map_path, runbooks_path, …)
  2. Files in AGENT_PROFILE_DIR
  3. Built-in preset named by ``extends:`` in those files
  4. Built-in ``generic-prometheus`` defaults
"""
from __future__ import annotations

import copy
import functools
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .models import (
    ExecutionProfile,
    LogsProfile,
    MetricsProfile,
    PromptProfile,
    RedactionProfile,
)

logger = logging.getLogger(__name__)

_PRESETS_DIR = Path(__file__).resolve().parent / "presets"

# Every preset chain is rooted here so a partial preset (e.g. spring-micrometer,
# which only defines metrics) can never resolve a section to nothing. Without
# this, redaction silently becomes a no-op — see tests/test_profile_loader.py.
_BASE_PRESET = "generic-prometheus"

# Profile section -> filename within a profile directory / preset.
_SECTION_FILES = {
    "metrics": "metrics_profile.yaml",
    "logs": "logs_profile.yaml",
    "redaction": "redaction.yaml",
    "prompt": "prompt_profile.yaml",
    "service_map": "service_map.yaml",
}

# Lists that accumulate across an `extends:` chain instead of being replaced.
# Redaction rules are additive by nature: a host profile adds tenant/PII rules on
# top of the base secret scrubbing rather than discarding it. Entries are keyed
# by `name`, so a child can still override a parent rule by reusing its name.
_ADDITIVE_LIST_KEYS = {"rules"}


@dataclass(frozen=True)
class IntegrationProfile:
    """Resolved profile used by clients, nodes, redaction, and prompts."""

    name: str
    root: Path | None
    metrics: MetricsProfile
    logs: LogsProfile
    redaction: RedactionProfile
    prompt: PromptProfile
    service_map_path: str | None = None
    runbooks_path: str | None = None
    execution: ExecutionProfile = ExecutionProfile(version=1, image="", actions=())
    execution_profile_path: str | None = None
    # Unparseable profile YAML files (path + reason). Empty when load succeeded.
    # `diag validate` and agent startup treat these as hard failures so a broken
    # overlay cannot silently fall back to preset-only config.
    load_errors: tuple[str, ...] = ()


class ProfileLoadError(Exception):
    """Raised when a profile YAML file exists but cannot be parsed."""

    def __init__(self, path: Path, detail: str):
        self.path = path
        self.detail = detail
        super().__init__(f"Failed to load profile file {path}: {detail}")


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError) as exc:
        raise ProfileLoadError(path, str(exc)) from exc


def _merge_named_list(
    base: list[Any], overlay: list[Any]
) -> list[Any]:
    """Append overlay entries to base, overriding same-`name` dict entries."""
    merged = [copy.deepcopy(item) for item in base]
    index = {
        item["name"]: pos
        for pos, item in enumerate(merged)
        if isinstance(item, dict) and "name" in item
    }
    for item in overlay:
        name = item.get("name") if isinstance(item, dict) else None
        if name is not None and name in index:
            merged[index[name]] = copy.deepcopy(item)
        else:
            if name is not None:
                index[name] = len(merged)
            merged.append(copy.deepcopy(item))
    return merged


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge overlay onto base.

    Scalars and most lists in overlay replace the base value. Lists named in
    ``_ADDITIVE_LIST_KEYS`` accumulate instead (see that constant).
    """
    out = copy.deepcopy(base)
    for key, val in overlay.items():
        if key == "extends":
            continue
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        elif (
            key in _ADDITIVE_LIST_KEYS
            and isinstance(val, list)
            and isinstance(out.get(key), list)
        ):
            out[key] = _merge_named_list(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


def list_presets() -> list[str]:
    if not _PRESETS_DIR.is_dir():
        return []
    return sorted(p.name for p in _PRESETS_DIR.iterdir() if p.is_dir())


def load_preset(name: str) -> dict[str, dict[str, Any]]:
    """Return {metrics|logs|redaction|prompt|service_map: data} for a preset."""
    preset_dir = _PRESETS_DIR / name
    if not preset_dir.is_dir():
        logger.warning("Unknown preset %r (available: %s)", name, list_presets())
        return {}
    result: dict[str, dict[str, Any]] = {}
    for key, filename in _SECTION_FILES.items():
        data = _read_yaml(preset_dir / filename)
        if data:
            result[key] = data
    return result


def _resolve_section(
    section: str,
    profile_data: dict[str, Any],
    *,
    default_preset: str = _BASE_PRESET,
) -> dict[str, Any]:
    """Merge the preset chain (rooted at _BASE_PRESET) then overlay profile data."""
    extends = profile_data.get("extends") or default_preset
    chain: list[str] = []
    seen: set[str] = set()
    current: str | None = extends if isinstance(extends, str) else default_preset
    while current and current not in seen:
        seen.add(current)
        chain.append(current)
        preset = load_preset(current)
        parent = (preset.get(section) or {}).get("extends")
        current = parent if isinstance(parent, str) else None

    # Always resolve the base preset first so partial presets inherit the rest.
    if _BASE_PRESET not in seen:
        chain.append(_BASE_PRESET)

    merged: dict[str, Any] = {}
    for name in reversed(chain):
        preset_section = load_preset(name).get(section) or {}
        merged = _deep_merge(merged, preset_section)
    return _deep_merge(merged, profile_data)


def _load_profile_dir(
    profile_dir: Path | None,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    if profile_dir is None or not profile_dir.is_dir():
        return {}, []
    out: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for key, filename in _SECTION_FILES.items():
        path = profile_dir / filename
        if not path.is_file():
            continue
        try:
            data = _read_yaml(path)
        except ProfileLoadError as exc:
            logger.error("%s", exc)
            errors.append(str(exc))
            continue
        if data:
            out[key] = data
    return out, errors


def build_profile(
    *,
    profile_dir: str | Path | None = None,
    default_preset: str = _BASE_PRESET,
    service_map_override: str | None = None,
    runbooks_override: str | None = None,
) -> IntegrationProfile:
    """Build a fully resolved IntegrationProfile."""
    root: Path | None = None
    name = default_preset
    if profile_dir:
        root = Path(profile_dir).expanduser().resolve()
        name = root.name
        if not root.is_dir():
            logger.warning(
                "AGENT_PROFILE_DIR=%s is not a directory; using preset %s only",
                root,
                default_preset,
            )
            root = None
            name = default_preset

    file_data, load_errors = _load_profile_dir(root)

    metrics_raw = _resolve_section(
        "metrics", file_data.get("metrics") or {}, default_preset=default_preset
    )
    logs_raw = _resolve_section(
        "logs", file_data.get("logs") or {}, default_preset=default_preset
    )
    redaction_raw = _resolve_section(
        "redaction", file_data.get("redaction") or {}, default_preset=default_preset
    )
    prompt_raw = _resolve_section(
        "prompt", file_data.get("prompt") or {}, default_preset=default_preset
    )

    # service_map: explicit override, else the profile's own file. Topology is
    # deployment-specific, so presets deliberately do NOT ship one; without a
    # profile the agent runs with an empty dependency map (no blast radius).
    service_map_path: str | None = service_map_override
    if not service_map_path and root is not None:
        candidate = root / "service_map.yaml"
        if candidate.is_file():
            service_map_path = str(candidate)

    runbooks_path: str | None = runbooks_override
    if not runbooks_path and root is not None:
        candidate = root / "runbooks"
        if candidate.is_dir():
            runbooks_path = str(candidate)

    execution_data: dict[str, Any] = {}
    execution_profile_path: str | None = None
    if root is not None:
        exec_file = root / "execution_profile.yaml"
        if exec_file.is_file():
            try:
                execution_data = _read_yaml(exec_file)
                execution_profile_path = str(exec_file)
            except ProfileLoadError as exc:
                logger.error("%s", exc)
                load_errors.append(str(exc))

    logger.info(
        "Loaded integration profile name=%s dir=%s preset=%s",
        name,
        root,
        default_preset,
    )
    return IntegrationProfile(
        name=name,
        root=root,
        metrics=MetricsProfile.from_dict(metrics_raw),
        logs=LogsProfile.from_dict(logs_raw),
        redaction=RedactionProfile.from_dict(redaction_raw),
        prompt=PromptProfile.from_dict(prompt_raw),
        execution=ExecutionProfile.from_dict(execution_data),
        service_map_path=service_map_path,
        runbooks_path=runbooks_path,
        execution_profile_path=execution_profile_path,
        load_errors=tuple(load_errors),
    )


def get_profile() -> IntegrationProfile:
    """Return the process-wide profile (cached; driven by Settings)."""
    from ..config import settings

    return _cached_profile(
        settings.profile_dir or "",
        settings.default_preset,
        settings.service_map_path or "",
        settings.runbooks_path or "",
    )


@functools.lru_cache(maxsize=4)
def _cached_profile(
    profile_dir: str,
    default_preset: str,
    service_map_path: str,
    runbooks_path: str,
) -> IntegrationProfile:
    # When profile_dir is set, let the profile supply service_map/runbooks unless
    # the Settings paths were explicitly customized away from package defaults.
    # We always pass Settings paths as overrides so env AGENT_SERVICE_MAP_PATH wins.
    return build_profile(
        profile_dir=profile_dir or None,
        default_preset=default_preset,
        service_map_override=service_map_path or None,
        runbooks_override=runbooks_path or None,
    )


def reset_profile_cache() -> None:
    """Clear the cached profile (tests / hot-reload)."""
    _cached_profile.cache_clear()
