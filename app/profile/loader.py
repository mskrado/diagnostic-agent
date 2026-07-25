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

from .models import LogsProfile, MetricsProfile, PromptProfile, RedactionProfile

logger = logging.getLogger(__name__)

_PKG_ROOT = Path(__file__).resolve().parent.parent.parent
_PRESETS_DIR = Path(__file__).resolve().parent / "presets"

_PROFILE_FILES = (
    "metrics_profile.yaml",
    "logs_profile.yaml",
    "redaction.yaml",
    "prompt_profile.yaml",
    "service_map.yaml",
)


@dataclass(frozen=True)
class IntegrationProfile:
    """Resolved profile used by clients, nodes, redaction, and prompts."""

    name: str
    root: Path | None
    metrics: MetricsProfile
    logs: LogsProfile
    redaction: RedactionProfile
    prompt: PromptProfile
    service_map_path: str | None
    runbooks_path: str | None


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError) as exc:
        logger.error("Failed to load profile file %s: %s", path, exc)
        return {}


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge overlay onto base; lists/scalars in overlay replace."""
    out = copy.deepcopy(base)
    for key, val in overlay.items():
        if key == "extends":
            continue
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
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
    mapping = {
        "metrics": "metrics_profile.yaml",
        "logs": "logs_profile.yaml",
        "redaction": "redaction.yaml",
        "prompt": "prompt_profile.yaml",
        "service_map": "service_map.yaml",
    }
    for key, filename in mapping.items():
        data = _read_yaml(preset_dir / filename)
        if data:
            result[key] = data
    return result


def _resolve_section(
    section: str,
    profile_data: dict[str, Any],
    *,
    default_preset: str = "generic-prometheus",
) -> dict[str, Any]:
    """Merge preset chain then overlay profile section data."""
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

    merged: dict[str, Any] = {}
    for name in reversed(chain):
        preset_section = load_preset(name).get(section) or {}
        merged = _deep_merge(merged, preset_section)
    return _deep_merge(merged, profile_data)


def _load_profile_dir(profile_dir: Path | None) -> dict[str, dict[str, Any]]:
    if profile_dir is None or not profile_dir.is_dir():
        return {}
    mapping = {
        "metrics": "metrics_profile.yaml",
        "logs": "logs_profile.yaml",
        "redaction": "redaction.yaml",
        "prompt": "prompt_profile.yaml",
        "service_map": "service_map.yaml",
    }
    out: dict[str, dict[str, Any]] = {}
    for key, filename in mapping.items():
        data = _read_yaml(profile_dir / filename)
        if data:
            out[key] = data
    return out


def build_profile(
    *,
    profile_dir: str | Path | None = None,
    default_preset: str = "generic-prometheus",
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

    file_data = _load_profile_dir(root)

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

    # service_map: prefer explicit override, then profile file, then preset, else None
    service_map_path: str | None = service_map_override
    if not service_map_path and root is not None:
        candidate = root / "service_map.yaml"
        if candidate.is_file():
            service_map_path = str(candidate)
    if not service_map_path:
        preset_map = _PRESETS_DIR / default_preset / "service_map.yaml"
        if preset_map.is_file():
            service_map_path = str(preset_map)

    runbooks_path: str | None = runbooks_override
    if not runbooks_path and root is not None:
        candidate = root / "runbooks"
        if candidate.is_dir():
            runbooks_path = str(candidate)

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
        service_map_path=service_map_path,
        runbooks_path=runbooks_path,
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
