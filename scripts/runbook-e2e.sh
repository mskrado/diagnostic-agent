#!/usr/bin/env bash
# End-to-end runbook scenario runner. See docs/TESTING.md.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if command -v python3 >/dev/null 2>&1; then
  exec python3 "$ROOT/scripts/runbook-e2e.py" "$@"
fi
if command -v python >/dev/null 2>&1; then
  exec python "$ROOT/scripts/runbook-e2e.py" "$@"
fi
echo "python not found on PATH" >&2
exit 1
