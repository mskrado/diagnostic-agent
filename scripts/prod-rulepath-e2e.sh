#!/usr/bin/env bash
# PROD rule-path E2E wrapper. See docs/TESTING.md and scripts/prod-rulepath-e2e.ps1.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PS1="$ROOT/scripts/prod-rulepath-e2e.ps1"
if command -v pwsh >/dev/null 2>&1; then
  exec pwsh -NoProfile -ExecutionPolicy Bypass -File "$PS1" "$@"
fi
if command -v powershell.exe >/dev/null 2>&1; then
  exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PS1" "$@"
fi
if command -v powershell >/dev/null 2>&1; then
  exec powershell -NoProfile -ExecutionPolicy Bypass -File "$PS1" "$@"
fi
echo "PowerShell required on this host. From repo root run:" >&2
echo "  .\\scripts\\prod-rulepath-e2e.ps1" >&2
exit 1
