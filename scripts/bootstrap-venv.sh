#!/usr/bin/env bash
# Create a local virtualenv and install diagnostic-agent reproducibly.
#
# Uses requirements.lock (exact pins) for runtime deps, then editable install
# for the `diag` console script. Optional --dev also installs requirements-dev.txt.
#
# Prerequisites: Python >=3.11 on PATH (see .python-version / docs/CLIENT_FORK.md).
# On Amazon Linux 2, install OS packages first: ./scripts/install-system-deps.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WITH_DEV=0
PYTHON_BIN="${PYTHON:-}"
VENV_DIR="${VENV_DIR:-$ROOT/.venv}"

usage() {
  cat <<'EOF'
Usage: ./scripts/bootstrap-venv.sh [--dev] [--python PATH]

  --dev           Also install requirements-dev.txt (pytest, …)
  --python PATH   Interpreter to use (default: python3.12, then python3)
  -h, --help      Show this help

Environment:
  PYTHON     Same as --python
  VENV_DIR   Virtualenv location (default: ./.venv)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dev) WITH_DEV=1; shift ;;
    --python) PYTHON_BIN="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

pick_python() {
  if [[ -n "$PYTHON_BIN" ]]; then
    echo "$PYTHON_BIN"
    return
  fi
  local candidate
  for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      echo "$candidate"
      return
    fi
  done
  echo ""
}

PYTHON_BIN="$(pick_python)"
if [[ -z "$PYTHON_BIN" ]]; then
  cat <<'EOF' >&2
No Python interpreter found on PATH.

This project requires Python >=3.11 (see pyproject.toml / .python-version).

  Amazon Linux 2:  ./scripts/install-system-deps.sh   # then pyenv install 3.12
  Amazon Linux 2023 / Ubuntu: ./scripts/install-system-deps.sh
  Or run init without a host Python via Docker — see docs/CLIENT_FORK.md
EOF
  exit 1
fi

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  ver="$("$PYTHON_BIN" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
  echo "Python >=3.11 required; $PYTHON_BIN is $ver" >&2
  echo "On Amazon Linux 2 system python3 is too old — use pyenv 3.12 or Docker." >&2
  exit 1
fi

echo "==> Using $PYTHON_BIN ($("$PYTHON_BIN" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'))"
echo "==> Creating venv at $VENV_DIR"
"$PYTHON_BIN" -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

python -m pip install -U pip setuptools wheel

if [[ -f requirements.lock ]]; then
  echo "==> Installing runtime pins from requirements.lock"
  pip install -r requirements.lock
else
  echo "==> requirements.lock missing — falling back to requirements.txt" >&2
  pip install -r requirements.txt
fi

if [[ "$WITH_DEV" -eq 1 ]]; then
  echo "==> Installing dev dependencies from requirements-dev.txt"
  pip install -r requirements-dev.txt
fi

echo "==> Editable install (console scripts: diag)"
# Deps already installed from the lock; --no-deps keeps the pin set intact.
pip install --no-deps -e .

echo
echo "OK. Activate and run:"
echo "  source $VENV_DIR/bin/activate"
echo "  diag init"
echo "  # or: diag --help"
