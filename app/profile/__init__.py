"""Integration-profile loading.

An integration profile is a directory of YAML config that adapts the
project-agnostic agent core to a host stack (metrics naming, log labels,
redaction, prompt context, topology). See ``loader.get_profile``.
"""
from __future__ import annotations

from .loader import (
    IntegrationProfile,
    build_profile,
    get_profile,
    list_presets,
    load_preset,
    reset_profile_cache,
)
from .models import LogsProfile, MetricsProfile, PromptProfile, RedactionProfile

__all__ = [
    "IntegrationProfile",
    "LogsProfile",
    "MetricsProfile",
    "PromptProfile",
    "RedactionProfile",
    "build_profile",
    "get_profile",
    "list_presets",
    "load_preset",
    "reset_profile_cache",
]
