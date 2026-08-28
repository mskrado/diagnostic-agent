#!/usr/bin/env bash
# Build an offline update pack for air-gapped client forks.
# Usage: ./scripts/build-offline-pack.sh [VERSION]
# Output: dist/offline-pack-<VERSION>/{*.bundle,wheelhouse.tar.gz,base-image.tar}
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VERSION="${1:-$(git describe --tags --always 2>/dev/null | sed 's/^v//')}"
OUT="$ROOT/dist/offline-pack-${VERSION}"
BASE_IMAGE="${BASE_IMAGE:-python:3.12-slim}"

mkdir -p "$OUT"

echo "==> git bundle"
git bundle create "$OUT/diagnostic-agent-v${VERSION}.bundle" --all

echo "==> wheelhouse (from requirements.lock)"
WH="$OUT/wheelhouse"
mkdir -p "$WH"
if [[ -f requirements.lock ]]; then
  pip download -r requirements.lock -d "$WH"
else
  pip download -r requirements.txt -d "$WH"
fi
tar -czf "$OUT/wheelhouse.tar.gz" -C "$OUT" wheelhouse
rm -rf "$WH"

echo "==> base image tarball ($BASE_IMAGE)"
docker pull "$BASE_IMAGE"
docker save "$BASE_IMAGE" -o "$OUT/base-image.tar"

cat > "$OUT/README.txt" <<EOF
Offline update pack for diagnostic-agent v${VERSION}

1. Copy this directory to the air-gapped host.
2. docker load -i base-image.tar
3. diag upgrade --from-pack $(basename "$OUT")
4. Rebuild: ./client/scripts/start.sh

Wheelhouse: extract and pip install --no-index --find-links wheelhouse -r requirements.lock
EOF

echo "Pack ready: $OUT"
