#!/usr/bin/env bash
# Install OS packages needed to build Python >=3.11 and run diagnostic-agent.
# Detects Amazon Linux 2 / Amazon Linux 2023 / Ubuntu / Debian.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  SUDO="sudo"
else
  SUDO=""
fi

packages_from() {
  grep -vE '^\s*(#|$)' "$1" | tr '\n' ' '
}

if [[ -f /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
else
  echo "Cannot detect OS (/etc/os-release missing)" >&2
  exit 1
fi

case "${ID:-}-${VERSION_ID:-}" in
  amzn-2)
    echo "==> Amazon Linux 2: installing packages from deps/amazon-linux-2.txt"
    # shellcheck disable=SC2046
    $SUDO yum install -y $(packages_from deps/amazon-linux-2.txt)
    cat <<'EOF'

Next (AL2 has no Python >=3.11 in yum):
  curl https://pyenv.run | bash
  # add pyenv to ~/.bashrc, then:
  export CPPFLAGS="-I/usr/include/openssl11"
  export LDFLAGS="-L/usr/lib64/openssl11 -Wl,-rpath,/usr/lib64/openssl11"
  pyenv install 3.12
  pyenv local 3.12
  ./scripts/bootstrap-venv.sh

Or skip host Python and run init via Docker (see docs/CLIENT_FORK.md).
EOF
    ;;
  amzn-2023)
    echo "==> Amazon Linux 2023: installing Python 3.12 + build tools"
    $SUDO dnf install -y git python3.12 python3.12-pip python3.12-devel gcc make \
      openssl-devel libffi-devel zlib-devel bzip2-devel readline-devel \
      sqlite-devel xz-devel docker
    echo "Next: python3.12 -m venv .venv && ./scripts/bootstrap-venv.sh"
    ;;
  ubuntu-*|debian-*)
    echo "==> Debian/Ubuntu: installing packages from deps/ubuntu.txt"
    $SUDO apt-get update -qq
    # shellcheck disable=SC2046
    $SUDO apt-get install -y $(packages_from deps/ubuntu.txt)
    echo "Next: ./scripts/bootstrap-venv.sh"
    ;;
  *)
    echo "Unsupported OS: ID=${ID:-?} VERSION_ID=${VERSION_ID:-?}" >&2
    echo "Install Python >=3.11, then run ./scripts/bootstrap-venv.sh" >&2
    exit 1
    ;;
esac
