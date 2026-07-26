---
name: Release Checklist
about: Pre-flight checklist for a devel → main publish (GHCR / PyPI)
title: "[RELEASE] vX.Y.Z"
labels: ["release"]
---

**Target version**: vX.Y.Z
**Release manager**:
**Release PR**: devel → main #

## Scope

- Closes #
- Closes #

## Pre-merge checklist

- [ ] All in-scope child issues closed or explicitly deferred
- [ ] Breaking workspace / CLI / profile contract changes documented in `docs/`
- [ ] `.env.example` updated if new `AGENT_*` settings
- [ ] Host pin bump noted (image tag / `agent_version`) if hosts must upgrade
- [ ] CI green on `devel`

## Publish

- [ ] PR merged to `main`
- [ ] `release.yml` run from `main` (`-f bump=patch|minor|major`) **or** tag `vX.Y.Z` pushed
- [ ] `release.yml` succeeded (GHCR push, wheel, git tag, GitHub Release)
- [ ] `ghcr.io/mskrado/diagnostic-agent:X.Y.Z` pullable

## Follow-up

- [ ] Host repos (if any) opened issues/PRs to bump the pin
- [ ] Tech-debt / bug issues opened for anything found
- [ ] Release issue closed
