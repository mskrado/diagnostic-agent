"""Every checked-in file must be vendor-neutral.

This agent is a standalone product: host-specific names belong in the host's
workspace (`agent.yaml`, `service_map.yaml`, `prompt_profile.yaml`), never in
the golden source. Branding that leaks into shipped code, corpus or scripts
reaches every adopter — an alert email tagged with someone else's company, a
smoke script that targets containers which do not exist, or a runbook corpus
that teaches the model logger names from a stack it is not diagnosing.

Regression: a repo-wide audit found ~70 host-brand references across ~30 tracked
files, including a `docker-compose` container name in shipped scenario data that
no host could ever match.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Host brand tokens that must not appear in checked-in files. `publishing` is the
# ordinary English word, not the brand, so it is excluded from the match.
BRAND_PATTERN = re.compile(r"publishi(?!ng)", re.IGNORECASE)

# Paths where naming a downstream host repository is legitimate context rather
# than branding leakage. Only this guard qualifies today: the SDLC and design
# docs that used to be listed here describe the fork model now and name no host.
# Entries are checked for staleness by test_allowlist_has_no_stale_entries.
ALLOWED_PATHS = frozenset(
    {
        "tests/test_vendor_neutral.py",
    }
)

# Generated install bundles under deploy/ are host-specific by definition and are
# not tracked; excluded here so a stray local checkout cannot fail the guard.
EXCLUDED_PREFIXES = ("deploy/",)

BINARY_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz"})


def _tracked_files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git unavailable; cannot enumerate tracked files")
    return [line for line in out.stdout.splitlines() if line.strip()]


def test_no_host_branding_in_tracked_files():
    offenders: list[str] = []

    for rel in _tracked_files():
        if rel in ALLOWED_PATHS or rel.startswith(EXCLUDED_PREFIXES):
            continue
        path = REPO_ROOT / rel
        if path.suffix.lower() in BINARY_SUFFIXES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if BRAND_PATTERN.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()[:120]}")

    assert not offenders, (
        "Host branding found in checked-in files. Move host-specific names into "
        "the host's workspace config, or use a neutral placeholder "
        "(acme, com.example.*, <prefix>-<service>). If the reference is "
        "legitimate context about a downstream host repo, add the path to "
        "ALLOWED_PATHS with a reason.\n  " + "\n  ".join(offenders)
    )


def test_allowlist_has_no_stale_entries():
    """An allowlisted path that no longer needs it must be removed.

    A stale entry is a permanent, silent exemption: once the branding in that
    file is cleaned up, the path keeps its waiver and branding can be
    reintroduced there without the guard noticing.
    """
    stale: list[str] = []
    for rel in sorted(ALLOWED_PATHS):
        if rel == "tests/test_vendor_neutral.py":
            continue  # this file necessarily contains the pattern it matches
        path = REPO_ROOT / rel
        if not path.is_file():
            stale.append(f"{rel} (no longer exists)")
            continue
        if not BRAND_PATTERN.search(path.read_text(encoding="utf-8")):
            stale.append(f"{rel} (no branding left)")

    assert not stale, (
        "Remove these paths from ALLOWED_PATHS — they no longer contain host "
        "branding, so the waiver only hides future regressions:\n  "
        + "\n  ".join(stale)
    )


def test_guard_would_catch_reintroduced_branding():
    """The pattern matches branding but not the English word 'publishing'."""
    assert BRAND_PATTERN.search("container_name: publishi-platform-service")
    assert BRAND_PATTERN.search("com.publishi.platform.health.JvmHealthIndicator")
    assert not BRAND_PATTERN.search("Publishing others' private information")
    assert not BRAND_PATTERN.search("git tag is created once publishing succeeded")
