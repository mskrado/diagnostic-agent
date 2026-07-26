## Summary

<!-- What does this PR change and why? -->

## Linked issue

Closes #

## Contribution type

- [ ] Core agent code
- [ ] Integration profile / preset
- [ ] Runbook + eval case + scenario (preferred corpus contribution)
- [ ] Docs / CI

## Test plan

- [ ] `pytest -q` passes locally
- [ ] `diag lint` / `python scripts/corpus_lint.py` passes (for corpus changes)
- [ ] No real PII / tenant data in eval logs
- [ ] Hypotheses-only invariant preserved
- [ ] Commits are DCO signed (`git commit -s`)

## Release / host impact

- [ ] No publish needed (lands on `devel` only for now)
- [ ] Needs GHCR/PyPI publish after `devel` → `main`
- [ ] Host repos must bump image tag / `agent_version` after release
