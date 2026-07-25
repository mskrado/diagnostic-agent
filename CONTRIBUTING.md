# Contributing

Thanks for helping improve diagnostic-agent.

## The contribution loop (preferred)

To add a diagnostic capability, submit **all three**:

1. A runbook under `runbooks/` or your profile's `runbooks/` (use `runbooks/_TEMPLATE-runbook.md`)
2. A case in `eval/blind_eval_dataset.yaml` (synthetic logs + ground truth — **no real PII/tenant data**)
3. A scenario in `runbook_scenarios.yaml` (matching alert labels)

CI runs a **no-LLM corpus lint** on every PR (schema, runbook↔scenario pairing,
`must_reference` tokens present in case logs, hypotheses-only wording).
Optional LLM eval is maintainer-triggered only (no secrets on untrusted PRs).

## Development

```bash
python -m venv .venv
pip install -r requirements.txt
pytest -q
```

For profile/loader work, also run:

```bash
pytest tests/test_profile_loader.py -q
```

## DCO (Developer Certificate of Origin)

Every commit must be signed off:

```bash
git commit -s -m "feat: add postgres timeout runbook"
```

This certifies the contribution under the [DCO 1.1](https://developercertificate.org/).

## Safety invariants (do not weaken)

- Hypotheses only — no auto-remediation in runbooks or prompt profiles
- Evidence must cite metrics/logs the agent was given
- Contributed eval logs must be synthetic or fully redacted
- Core system-prompt invariants are not overridable via `prompt_profile.yaml`

## Pull requests

- One atomic change per PR
- Include tests for new Python modules
- Update `docs/INTEGRATING.md` if the profile schema changes
