#!/usr/bin/env bash
# Compute the next semantic version from the latest git tag.
#
# Requires full tag history (fetch-depth: 0 in CI).
#
# Usage:
#   bash scripts/version-next.sh            # default: patch bump
#   BUMP=minor bash scripts/version-next.sh
#   BUMP=major bash scripts/version-next.sh
#
# Output: version string without the 'v' prefix, e.g. "1.1.2"

set -euo pipefail

BUMP="${BUMP:-patch}"

LAST_TAG=$(git describe --tags --match 'v[0-9]*' --abbrev=0 2>/dev/null || echo "")

if [ -z "$LAST_TAG" ]; then
  echo "1.0.0"
  exit 0
fi

VERSION="${LAST_TAG#v}"
IFS='.' read -r MAJOR MINOR PATCH <<< "$VERSION"

case "$BUMP" in
  major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
  minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
  patch) PATCH=$((PATCH + 1)) ;;
  *) echo "Invalid BUMP type: $BUMP (expected: patch, minor, major)" >&2; exit 1 ;;
esac

echo "${MAJOR}.${MINOR}.${PATCH}"
