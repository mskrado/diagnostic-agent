# `diag drift` — report what no longer matches the workspace

Once a workspace exists, the stack keeps changing: new services appear, alerts
are renamed, metric names shift, map nodes go dark. `diag drift` re-scans (or
reads a saved [`diag scan`](SCAN.md) bundle) and reports what no longer matches.

It is a **gate**, not a writer. It does not modify workspace files. Exit code
`1` means error-class drift; `0` means the workspace still covers what the
evidence shows (notes alone are fine).

```bash
# Against a live stack (URLs from the workspace / env)
diag drift -w client/workspace

# Against a saved scan bundle (CI / air-gap)
diag drift -w client/workspace --bundle ./scan-evidence.json --no-oracle

# Machine-readable
diag drift -w client/workspace --json --out drift-report.json
```

## What it checks

| Drift | Severity | Signal |
|---|---|---|
| **New service, no map node** | error | Prometheus/Loki service candidate absent from `service_map.yaml` |
| **Map node gone** | error | Node in `service_map.yaml` with no metrics and no logs behind it |
| **Alert with no scenario** | error | Ruler alertname not covered by `scenarios.yaml` |
| **Dead metrics template** | error | Active profile template returns no data for the probe service |
| **Dead log selector** | error | `logs_profile` `service_label` returns no lines in the lookback window |
| **Unused scenario** | note | Scenario alertname no longer present in any ruler |

Notes (unused scenarios) do **not** fail the gate — they are a cleanup hint.
Live template and log-selector checks need a reachable Prometheus/Loki (or
`--prometheus-url` / `--loki-url`). With `--bundle` alone and no URLs, those
checks are skipped and reported as a warning so air-gapped CI can still gate
topology and alert coverage.

## Client CI hook

`diag init` scaffolds `.github/workflows/client-validate.yml` with an optional
drift step. Set the repository variable `DRIFT_BUNDLE` to a path in the repo
(for example a committed scan artifact refreshed by a scheduled job) and the
step runs:

```yaml
- name: Drift check (optional)
  if: ${{ vars.DRIFT_BUNDLE != '' }}
  run: >
    diag drift -w client/workspace
    --bundle "${{ vars.DRIFT_BUNDLE }}"
    --no-oracle
```

Live drift (`diag drift -w …` against Prometheus) belongs on a runner that can
reach the stack — a scheduled job or a self-hosted pre-merge check — not on
GitHub-hosted Actions unless the stack is reachable from there.

## Related

- [SCAN.md](SCAN.md) — collecting the evidence bundle
- [DRAFT.md](DRAFT.md) — writing the workspace the evidence supports
- [WORKSPACE.md](WORKSPACE.md) — what each workspace file is for
- [INSTALL.md](INSTALL.md) — `diag init` and the client layout
