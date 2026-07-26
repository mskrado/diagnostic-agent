#!/usr/bin/env bash
# Thin wrapper: diag install "$@"
set -euo pipefail
exec diag install "$@"
