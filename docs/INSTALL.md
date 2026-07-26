# Installing diagnostic-agent against an existing observability stack

`diag install` discovers Prometheus, Loki, Alertmanager, Grafana (and related
tools) on a target host, collects every parameter the agent needs, and writes a
complete install bundle to a directory you choose.

```bash
diag install --output ./deploy
diag install --output ./deploy --dry-run
diag install --target ops.example.com --ssh ec2-user@ops.example.com \
             --output ./deploy --non-interactive --yes
diag install --output ./deploy --apply --start
```

## What it generates

```text
<output>/
├── agent/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── .env                 # secrets — gitignored
│   └── workspace/           # agent.yaml, profile, runbooks, scenarios
├── observability/
│   ├── prometheus/alert-rules.generated.yml
│   ├── alertmanager/route.generated.yml
│   ├── promtail/promtail.generated.yaml
│   └── grafana/README.md
├── install-report.json      # discovery inventory (secrets redacted)
└── APPLY.md                 # step-by-step wiring instructions
```

## Phases

1. **Discover** — Docker introspection (local or `--ssh`), HTTP health/version
   probes, bounded port scan. Builds a reachability matrix so agent→tools and
   Alertmanager→agent webhook addresses are correct for the placement.
2. **Collect** — Resolve endpoints, preset, LLM/embeddings, SMTP, Grafana token.
   Precedence: discovered → flag/env → interactive prompt → safe default.
3. **Generate** — Write the bundle above. Alert rules are the intersection of
   the shipped runbook corpus (only alerts the agent can diagnose).
4. **Verify** — Confirm redaction, workspace load, and alert-rule YAML. Optional
   `promtool` when on `PATH`.

`--apply` best-effort reloads Prometheus/Alertmanager. `--start` runs
`docker compose up -d` for the generated agent and probes `/health`.

## Graceful degradation

| Missing tool   | Behavior                                              |
|----------------|-------------------------------------------------------|
| Prometheus     | **Hard fail** (metrics are mandatory)                 |
| Loki           | Metrics-only diagnosis                                |
| Alertmanager   | No webhook route; agent still accepts `POST /alert`   |
| Grafana        | Annotations disabled                                  |

## Requirements

- Python 3.11+ with the `diagnostic-agent` package installed (`pip install -e .`)
- Optional: Docker CLI (for introspection and `--start`), `ssh` (for `--ssh`),
  `promtool` (for rule lint)

See also [INTEGRATING.md](INTEGRATING.md) and [WORKSPACE.md](WORKSPACE.md).
