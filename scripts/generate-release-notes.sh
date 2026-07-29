#!/usr/bin/env bash
# Generate release notes from PRs merged into devel (not from thin devel→main PRs).
#
# Source of truth for diagnostic-agent releases: feature PRs into `devel` since
# the previous release tag. Categories / excludes mirror `.github/release.yml`
# (keep in sync when editing either file).
#
# Requires: gh, jq, git (fetch-depth: 0 recommended for tag resolution).
#
# Usage:
#   bash scripts/generate-release-notes.sh
#   bash scripts/generate-release-notes.sh --since-tag v1.1.2 --current-tag v1.1.3
#   bash scripts/generate-release-notes.sh --since-tag v1.1.2 --until-tag v1.1.3 --out notes.md
#   bash scripts/generate-release-notes.sh --allow-empty
#
# Output: markdown on stdout (or --out file). Exit 1 if no in-scope PRs unless
# --allow-empty.

set -euo pipefail

SINCE_TAG=""
UNTIL_TAG=""
CURRENT_TAG=""
BASE="devel"
OUT=""
ALLOW_EMPTY=0

usage() {
  sed -n '2,18p' "$0" | sed 's/^# \?//'
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --since-tag)   SINCE_TAG="$2"; shift 2 ;;
    --until-tag)   UNTIL_TAG="$2"; shift 2 ;;
    --current-tag) CURRENT_TAG="$2"; shift 2 ;;
    --base)        BASE="$2"; shift 2 ;;
    --out)         OUT="$2"; shift 2 ;;
    --allow-empty) ALLOW_EMPTY=1; shift ;;
    -h|--help)     usage ;;
    *) echo "Unknown argument: $1" >&2; usage ;;
  esac
done

if ! command -v gh >/dev/null 2>&1; then
  echo "error: gh (GitHub CLI) is required" >&2
  exit 1
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "error: jq is required" >&2
  exit 1
fi

if [[ -z "$SINCE_TAG" ]]; then
  SINCE_TAG=$(git describe --tags --match 'v[0-9]*' --abbrev=0 2>/dev/null || true)
  if [[ -z "$SINCE_TAG" ]]; then
    SINCE_TAG=$(gh release list --limit 1 --json tagName -q '.[0].tagName // empty' 2>/dev/null || true)
  fi
fi
if [[ -z "$SINCE_TAG" ]]; then
  echo "error: no previous release tag found; pass --since-tag vX.Y.Z" >&2
  exit 1
fi

tag_date() {
  local tag="$1"
  git log -1 --format=%cI "$tag" 2>/dev/null \
    || git log -1 --format=%cI "${tag}^{}" 2>/dev/null \
    || gh release view "$tag" --json publishedAt -q .publishedAt
}

SINCE_DATE=$(tag_date "$SINCE_TAG")
if [[ -z "$SINCE_DATE" ]]; then
  echo "error: could not resolve date for tag $SINCE_TAG" >&2
  exit 1
fi

if [[ -n "$UNTIL_TAG" ]]; then
  UNTIL_DATE=$(tag_date "$UNTIL_TAG")
else
  UNTIL_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
fi

if [[ -z "$CURRENT_TAG" ]]; then
  if [[ -n "$UNTIL_TAG" ]]; then
    CURRENT_TAG="$UNTIL_TAG"
  else
    CURRENT_TAG="HEAD"
  fi
fi

REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)

# Keep aligned with .github/release.yml (first match wins; "*" is catch-all).
CATEGORIES=(
  "Breaking Changes|breaking-change,type:breaking"
  "Features|type:feature,feature,enhancement"
  "Bug Fixes|type:bug,bug,fix"
  "Tech Debt & Refactoring|type:tech-debt,refactor"
  "Documentation|type:docs,documentation"
  "Install & CI|area:ci,area:install,ci,infra"
  "Other Changes|*"
)

EXCLUDE_LABELS="ignore-for-release duplicate invalid wontfix"
EXCLUDE_AUTHORS="dependabot dependabot[bot] github-actions github-actions[bot]"

PRS_JSON=$(gh pr list \
  --base "$BASE" \
  --state merged \
  --limit 200 \
  --json number,title,url,mergedAt,author,labels)

FILTERED=$(printf '%s' "$PRS_JSON" | jq \
  --arg since "$SINCE_DATE" \
  --arg until "$UNTIL_DATE" \
  --arg excl_labels "$EXCLUDE_LABELS" \
  --arg excl_authors "$EXCLUDE_AUTHORS" '
  ($excl_labels | split(" ")) as $elabs |
  ($excl_authors | split(" ")) as $eauth |
  map(select(
    (.mergedAt > $since) and (.mergedAt <= $until)
    and ((.author.login // "") as $a | ($eauth | index($a) | not))
    and (([.labels[].name] | any(. as $l | $elabs | index($l))) | not)
    and ((.title | test("^Devel$"; "i")) | not)
    and ((.title | test("^docs\\(changelog\\)"; "i")) | not)
    and ((.title | test("^Release:"; "i")) | not)
  ))
  | sort_by(.mergedAt)
')

COUNT=$(printf '%s' "$FILTERED" | jq 'length')
if [[ "$COUNT" -eq 0 && "$ALLOW_EMPTY" -ne 1 ]]; then
  echo "error: no in-scope PRs merged into ${BASE} between ${SINCE_TAG} (${SINCE_DATE}) and ${UNTIL_DATE}" >&2
  echo "hint: pass --allow-empty for an intentional empty release, or check --since-tag" >&2
  exit 1
fi

ASSIGNED=$(printf '%s' "$FILTERED" | jq -c --arg cats_raw "$(printf '%s\n' "${CATEGORIES[@]}")" '
  ($cats_raw | split("\n") | map(select(length>0))
    | map(split("|") | {title: .[0], labels: (.[1] | split(","))})) as $cats |
  . as $prs |
  reduce $prs[] as $pr (
    {sections: ($cats | map({title: .title, items: []}))};
    ($pr.labels | map(.name)) as $plabels |
    ([
      range(0; $cats|length) as $i
      | select(
          ($cats[$i].labels | index("*"))
          or (any($plabels[]; . as $l | $cats[$i].labels | index($l)))
        )
      | $i
    ][0] // (($cats|length)-1)) as $idx |
    .sections[$idx].items += [$pr]
  ) | .sections
')

TMP=$(mktemp)
{
  echo "## What's Changed"
  echo ""
  printf '%s' "$ASSIGNED" | jq -r '
    .[] | select((.items | length) > 0) | (
      "### \(.title)\n",
      (.items[] | "* \(.title) (#\(.number)) by @\(.author.login) in \(.url)"),
      ""
    )
  '
  if [[ "$COUNT" -eq 0 ]]; then
    echo "_No in-scope pull requests in this range._"
    echo ""
  fi
  echo "**Full Changelog**: https://github.com/${REPO}/compare/${SINCE_TAG}...${CURRENT_TAG}"
} > "$TMP"

if [[ -n "$OUT" ]]; then
  cp "$TMP" "$OUT"
  echo "Wrote $OUT ($COUNT PR(s))" >&2
else
  cat "$TMP"
fi
rm -f "$TMP"
