# diagnostic-agent SDLC Guide

End-to-end software development lifecycle for **diagnostic-agent** — the same practice as [publishi.ai](https://github.com/mskrado/publishi.ai) (`docs/SDLC_GUIDE.md`), adapted for this Python agent repo (pytest / corpus lint, GHCR image, optional PyPI).

Host projects (for example publishi.ai) keep **thin** workspace config under their tree and pin a published image. **Golden agent source lives here.**

---

## Table of Contents

Topics:

1. [Architecture at a Glance](#1-architecture-at-a-glance)
2. [Environments](#2-environments)
3. [Local Development](#3-local-development)
4. [Branching Strategy and Code Review](#4-branching-strategy-and-code-review)
5. [Requirements & Issue Workflow](#5-requirements--issue-workflow)
6. [CI/CD Pipelines](#6-cicd-pipelines)
7. [Release](#7-release)
8. [Relationship to host repositories](#8-relationship-to-host-repositories)
9. [Agent playbook](#9-agent-playbook-deterministic-recipe)

---

## 1. Architecture at a Glance

```
                    ┌─────────────────────────────────┐
                    │        Developer laptop         │
                    │  venv + pytest + diag lint      │
                    │  optional local Ollama / LLM    │
                    └────────────────┬────────────────┘
                                     │  git push
                    ┌────────────────▼────────────────┐
                    │         GitHub Actions          │
                    │  PR / push → ci.yml             │
                    │  tag / dispatch → release.yml   │
                    │    → ghcr.io/.../diagnostic-agent
                    │    → PyPI (on v* tags)          │
                    └────────────────┬────────────────┘
                                     │  image pin
                    ┌────────────────▼────────────────┐
                    │     Host project workspace      │
                    │  agent.yaml + profile/runbooks  │
                    │  (e.g. publishi thin client)    │
                    └─────────────────────────────────┘
```

| Concern | Where it lives |
|---------|----------------|
| Agent runtime, tools, presets, default corpus | This repo (`mskrado/diagnostic-agent`) |
| Host topology, redaction, runbooks, scenarios | Host workspace (see `docs/WORKSPACE.md`) |
| Install / discover wiring | `docs/INSTALL.md` (`diag install`) |

---

## 2. Environments

| Environment | Purpose |
|-------------|---------|
| **Local** | `pip install -e ".[dev]"`, `pytest`, `diag lint` / `diag validate` against this repo or an example workspace |
| **CI** | Every PR and every push to `devel` / `main` — unit tests, corpus lint, example validate, Docker build |
| **Published image** | `ghcr.io/mskrado/diagnostic-agent:<semver>` (and `:latest`) after an explicit release |
| **Host DEV/PROD** | Host compose/GitOps pins the image tag; not deployed by this repo’s merge alone |

---

## 3. Local Development

```bash
git clone https://github.com/mskrado/diagnostic-agent.git
cd diagnostic-agent
python -m venv .venv
# Windows: .venv\Scripts\activate
# Unix:    source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
diag lint
diag validate -w examples/hello-world
```

Safety invariants (do not weaken): hypotheses only, synthetic/redacted eval data, evidence citing. See [CONTRIBUTING.md](../CONTRIBUTING.md).

Which test layers exist, what each proves, and which gate they belong to:
[TESTING_STRATEGY.md](TESTING_STRATEGY.md). Operator commands for a deployed
agent: [TESTING.md](TESTING.md).

Every commit must be **DCO signed**: `git commit -s`.

---

## 4. Branching Strategy and Code Review

```
main ─────────────────────────────────────────────►  (published releases)
  ▲                                                    │
  │  release PR only                                   │  merge PR
  │  (from devel)                                      │
devel ────────────────────────────────────────────►  (integration)
  ▲                                                    │
  │  feature PR only                                   │  PR review
  │                                                    │
feature/sdlc-guide-9 ─────────────────────────────►  (developer work)
```

| Branch | Purpose | CI | Publish |
|--------|---------|----|---------|
| `feature/*-<issue>` (also `fix/` / `perf/`) | Development — **issue number required in the name** | `ci.yml` on PR | None |
| `devel` | Integration — **all feature work lands here** | `ci.yml` on push | None |
| `main` | Release line — **only accepts merges from `devel`** | `ci.yml` on push | **Separate:** `release.yml` via tag or dispatch |

### Branch policy (mandatory)

| Allowed | Not allowed |
|---------|-------------|
| `feature/*-<issue>` → `devel` (PR) | Feature PR → `main` |
| `devel` → `main` (release PR only) | Stack of feature branches into `main` |
| Hotfix from `main`, then back-merge to `devel` | Retargeting a feature PR to `main` to skip integration |

### PR workflow

1. Create `feature/<short-slug>-<issue>` from **`devel`** (issue number **required**).
2. Open a **draft PR early** with **base `devel`**, and apply **PR labels at create time** (at least one `type:*`, plus `area:*` as appropriate). See [§5 Step 4](#5-requirements--issue-workflow).
3. CI (`ci.yml` + `dco.yml`) must be green.
4. Squash-merge to `devel` and delete the branch.
5. For a published release: open a **Release Checklist** issue and PR **`devel` → `main`** with a feature-PR inventory (prefer `scripts/open-release-pr.ps1`).
6. **After** merge to `main`: run Release explicitly (see [§7](#7-release)). Merge alone does **not** publish.

---

## 5. Requirements & Issue Workflow

GitHub Issues are the single source of truth. Every change traces back to an issue. Humans use the portal or `gh`; agents use `gh`/`git` — same objects.

> Prerequisite: `gh auth login` (or `GH_TOKEN`). Verify with `gh auth status`.

### Templates (`.github/ISSUE_TEMPLATE/`)

| Template | Use for |
|----------|---------|
| Feature / use case | New capability; corpus contribution plan |
| Bug report | Defects |
| Tech debt | Refactors / deferred scope |
| Release checklist | One per `devel` → `main` publish |

### Labels (create once)

```bash
gh label create "type:feature"   --color 1d76db --description "New capability"
gh label create "type:bug"       --color d73a4a --description "Defect"
gh label create "type:tech-debt" --color fbca04 --description "Refactor/maintenance"
gh label create "type:docs"      --color 0075ca --description "Documentation"
gh label create "ignore-for-release" --color cfd3d7 --description "Omit from release notes / CHANGELOG"
gh label create "area:core"      --color 5319e7 --description "Agent runtime / tools"
gh label create "area:corpus"    --color 5319e7 --description "Runbooks, scenarios, blind-eval"
gh label create "area:install"   --color 5319e7 --description "diag install / discovery"
gh label create "area:ci"        --color 5319e7 --description "CI / release workflows"
gh label create "area:docs"      --color 5319e7 --description "Documentation"
gh label create "priority:P0"    --color b60205
gh label create "priority:P1"    --color d93f0b
gh label create "priority:P2"    --color fbca04
gh label create "priority:P3"    --color c2e0c6
gh label create "status:needs-spec" --color ededed
gh label create "status:ready"      --color ededed
gh label create "status:in-progress" --color ededed
gh label create "status:blocked"    --color ededed
```

### End-to-end flow

**Step 1 — Intake.** Create an issue (`type:*`, `area:*`, `priority:*`, `status:needs-spec` or `status:ready`).

**Step 2 — Spec.** Schema / API / install-contract changes need a short design note or linked doc PR before coding; then `status:ready`. Corpus-only or typo docs may go straight to `ready`.

**Step 3 — Plan.** Work longer than ~3 hours → epic + child issues (one branch/PR per child).

**Step 4 — Build.**

```bash
git checkout devel && git pull
git checkout -b feature/<slug>-<issue>
git push -u origin HEAD
gh issue edit <issue> --add-label "status:in-progress"
gh pr create --draft --base devel --head feature/<slug>-<issue> \
  --title "[area] description (#<issue>)" \
  --label "type:feature,area:core" \
  --body "Closes #<issue>"
```

Copy the issue’s `type:*` / `area:*` onto the PR (example above). Release notes categorize **PR** labels, not issue labels — missing `type:*` lands the PR under “Other Changes” (`ci.yml` posts an advisory). Use `ignore-for-release` instead of `type:*` for changelog / chore noise.

**Step 5 — Verify.** Fill the PR template. Confirm the PR has a release-category label (`type:*` or `ignore-for-release`). `gh pr ready` then `gh pr checks --watch`.

**Step 6 — Ship feature.** Squash-merge into **`devel`**. `Closes #` is closed by `close-issues-on-devel-merge.yml` (native GitHub close-on-default-branch does not apply to `devel`).

**Step 7 — Publish (optional).** Release Checklist issue → PR `devel` → `main` → run `release.yml` or push `vX.Y.Z`.

---

## 6. CI/CD Pipelines

### `ci.yml`

**Triggers:** push to `main` / `devel`, all pull requests.

```
pytest → diag lint → diag validate (examples) → diag health-check → docker build
```

### `dco.yml`

Enforces Developer Certificate of Origin (`Signed-off-by:` on every commit).

### `close-issues-on-devel-merge.yml`

When a PR **merges into `devel`**, closes issues referenced with `Closes` / `Fixes` / `Resolves`.

### `release.yml`

**Triggers:** push of tag `vX.Y.Z`, or `workflow_dispatch` with a `bump` choice
(`patch` / `minor` / `major`) — there is no free-text version input.

A tag push *is* the version. A manual run computes the next semver from the
latest `v*` tag (`scripts/version-next.sh`), rejects anything that is not
`MAJOR.MINOR.PATCH`, refuses to run off `main`, and creates the git tag only
after the publish steps succeed.

The resolved version is stamped into `pyproject.toml` before build (not
committed), so the image, the wheel, `diag version`, and the audit record's
`agent_version` cannot disagree with the tag. Publishes a multi-arch image to
GHCR tagged `X.Y.Z`, `sha-<short>`, and `latest`, uploads the wheel to PyPI when
`PYPI_API_TOKEN` is configured, and opens a GitHub Release whose **What's Changed**
list is built from PRs merged into `devel` (see below) — not from thin `devel` →
`main` PRs.

---

## 7. Release

1. Open a **Release Checklist** issue **and** a `devel` → `main` PR whose body
   inventories feature PRs (prefer the helper script so the PR is never titled
   bare `Devel`):

```bash
# Preferred: generate inventory + checklist + draft PR
pwsh ./scripts/open-release-pr.ps1

# Or manually:
bash scripts/generate-release-notes.sh --out release-notes.md
gh pr create --base main --head devel \
  --title "Release: $(date -u +%Y-%m-%d) (devel → main)" \
  --body-file release-notes.md
```

2. Merge the release PR when CI is green.
3. Publish (merge does **not** publish):

```bash
# Option A — dispatch a bump; the workflow computes and pushes the tag
gh workflow run release.yml --ref main -f bump=patch
gh run watch

# Option B — pick the version yourself by pushing the tag
git checkout main && git pull
git tag v1.2.0
git push origin v1.2.0
```

**Release notes** are generated from **PRs merged into `devel`** since the
previous release tag by [`scripts/generate-release-notes.sh`](../scripts/generate-release-notes.sh)
(categories/excludes aligned with [`.github/release.yml`](../.github/release.yml)).
`release.yml` prepends an artifacts footer and sets `generate_release_notes: false`
so GitHub does not append a second list that only saw thin `devel` → `main` PRs.
The same inventory should appear in the **Release PR body**. PRs into `devel`
should carry a `type:*` label so categories are not all “Other Changes”
(`ci.yml` posts an advisory comment when missing).

Bump rules: **MAJOR** = breaking workspace/profile/CLI contract · **MINOR** = backward-compatible feature · **PATCH** = fix/docs/corpus.

Preview what a bump would produce before dispatching:

```bash
git fetch --tags
BUMP=minor bash scripts/version-next.sh
```

Host repos pin `agent_version` / image tag to the published semver (see `docs/INTEGRATING.md` and `docs/WORKSPACE.md`).

---

## 8. Relationship to host repositories

| Change | Land in |
|--------|---------|
| Agent code, presets, default runbooks, `diag` CLI, install scripts | **This repo** |
| Host `agent.yaml`, service map, redaction, host runbooks, compose pins | **Host repo** (e.g. publishi.ai `infrastructure/diagnostic-agent/`) |

Do not re-vendor agent source into the host. Prefer pulling `ghcr.io/mskrado/diagnostic-agent:<tag>`.

---

## 9. Agent playbook (deterministic recipe)

Cursor agents follow the same contract via `.cursor/rules/requirements-workflow.mdc`.

1. **No code without an issue.** Create one first if needed; announce its number.
2. **Respect `status:needs-spec`.** Do not branch until `status:ready` (unless the issue is already ready).
3. **One atomic task per branch/PR.** Branch from `devel`: `feature/<slug>-<issue>`.
4. **Print a session summary before starting work:**

   | | |
   |---|---|
   | **Issue** | `https://github.com/mskrado/diagnostic-agent/issues/<n>` |
   | **Draft PR** | `#<pr>` (or `(creating…)`) |
   | **Branch** | `feature/<slug>-<n>` |

5. **Feature PRs target `devel` only.** `main` receives merges from `devel` only.
6. **Always link.** Title `(#n)`; body `Closes #n` (or `Refs #n`). On `gh pr create`, pass `--label "type:…,area:…"` (or `ignore-for-release`) so release notes can categorize the PR.
7. **Wait for green CI.** Never request merge on red.
8. **Do not self-merge** protected branches; request review.
9. **Record release impact** in the PR (image/PyPI publish needed? host pin bump?).
10. **Close the loop.** On merge to `devel`, issues close via workflow; open tech-debt/bug issues for residue — do not reopen merged PRs.
11. **Commit finished work to the linked PR** with `git commit -s`, push, and leave `git status` clean for in-scope work. Unrelated leftovers get a **new** issue + branch + draft PR.

```bash
git status -sb
git add <in-scope paths>
git commit -s -m "docs: …"
git push -u origin HEAD
gh pr view --json url,commits
```
