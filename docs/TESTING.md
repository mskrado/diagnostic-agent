# Testing a deployed diagnostic-agent

Golden **operator E2E tooling** lives in this repository. Host projects (for
example publishi.ai) keep only thin wrappers or compose/workspace wiring — not
duplicate smoke-script bodies.

This page is the **command reference**. For which test layers exist, what each
one proves, what must be configured, and which are safe against production, read
[TESTING_STRATEGY.md](TESTING_STRATEGY.md) first.

Related:

| Tool | Purpose |
|---|---|
| [`diag validate` / `diag lint`](WORKSPACE.md) | Offline workspace / corpus checks |
| [`diag e2e`](INTEGRATING.md) | Live POST of `scenarios.yaml` entries to a running agent |
| [`eval/`](../eval/README.md) | Blind-eval **cases** (`blind_eval_dataset.yaml`) + judge |
| `scripts/smoke-test.*` | DEV stack smoke: health, `/alert` or Alertmanager, audit, Mailpit, Grafana |
| `scripts/prod-rulepath-e2e.*` | Remote SSH rule-path: Promtail → Loki ruler → AM → agent |
| `scripts/runbook-e2e.*` | Wrapper around `diag validate` / `lint` / `e2e` for a host workspace |

Host-owned **content** (not moved here):

- Workspace `scenarios.yaml` + `runbooks/` — alert → runbook pairs for `diag e2e`
- Host Alertmanager / Loki ruler rules that define `DiagnosticAgentSmokeMarker`
- Compose service names / EC2 SSH targets (pass as script parameters or env)

---

## 1. Prerequisites

- Docker (local smoke / runbook e2e) or SSH + identity file (remote rule-path)
- A running agent (`/health` → `status=ok`, `agent_initialized=true`)
- Observability stack the agent can reach (Prometheus, Loki, Grafana)
- Optional: Mailpit + Alertmanager for the default smoke path; Grafana token for annotations

Common env vars:

| Variable | Used by |
|---|---|
| `AGENT_E2E_CONTAINER_PREFIX` | Default compose name prefix (`publishi` → `publishi-diagnostic-agent`, …) |
| `AGENT_E2E_SSH_TARGET` | `user@host` for `prod-rulepath-e2e` |
| `AGENT_E2E_SSH_IDENTITY` | Path to SSH private key |
| `AGENT_E2E_URL` / `AGENT_E2E_WORKSPACE` | Live URL / workspace for `runbook-e2e` |
| `DIAGNOSTIC_AGENT_IMAGE` | Image tag for `runbook-e2e` docker runs |
| `AGENT_GRAFANA_TOKEN` or `DIAGNOSTIC_AGENT_GRAFANA_TOKEN` | Annotation check in smoke-test |

---

## 2. Local / DEV smoke (`scripts/smoke-test`)

```powershell
# From diagnostic-agent repo root
.\scripts\smoke-test.ps1 -ContainerPrefix publishi
.\scripts\smoke-test.ps1 -ContainerPrefix publishi -DirectAgent   # POST /alert only
.\scripts\smoke-test.ps1 -ContainerPrefix publishi -RulePath      # Loki ruler path
.\scripts\smoke-test.ps1 -ContainerPrefix publishi -RealPath      # stop a service (destructive)
```

```bash
./scripts/smoke-test.sh -ContainerPrefix publishi -DirectAgent
```

**Modes**

| Flag | What it exercises |
|---|---|
| *(default)* | POST synthetic alert to Alertmanager → agent webhook + dual Mailpit email + audit redaction |
| `-DirectAgent` | `POST /alert` only (diagnostic email / LLM path; no AM alert mail) |
| `-RulePath` | Emit sentinel log → Loki rule `DiagnosticAgentSmokeMarker` → AM → agent |
| `-RealPath` | Stop `-FaultContainer` and wait for a real alert (optional) |
| `-SkipGrafana` / `-SkipMailpit` | Skip optional checks |

Do **not** use `-RulePath` against a remote host via SSH *tunnels alone* — that
mode drives the **local** Docker daemon. Use §3 for remote rule-path.

---

## 3. Remote / PROD rule-path (`scripts/prod-rulepath-e2e`)

```powershell
.\scripts\prod-rulepath-e2e.ps1 `
  -SshTarget ec2-user@YOUR_HOST `
  -IdentityFile $HOME\.ssh\your-key.pem `
  -ContainerPrefix publishi

# Also open local tunnels for Grafana / Mailpit / agent review:
.\scripts\prod-rulepath-e2e.ps1 -SshTarget ec2-user@HOST -IdentityFile $HOME\.ssh\key.pem -OpenTunnels
```

Expect remote `PASS: DiagnosticAgentSmokeMarker audit record written`. Default
timeout is 420s (covers Alertmanager `group_interval` on repeat runs).

After tunnels:

```powershell
.\scripts\smoke-test.ps1 -ContainerPrefix publishi -AgentUrl http://localhost:8001 -DirectAgent
```

---

## 4. Runbook / scenario E2E (`scripts/runbook-e2e`)

Requires a host workspace with `scenarios.yaml` (see [WORKSPACE.md](WORKSPACE.md)).

```powershell
python scripts/runbook-e2e.py -w C:\path\to\host\infrastructure\diagnostic-agent --mode all
python scripts/runbook-e2e.py -w C:\path\to\ws --mode offline
python scripts/runbook-e2e.py -w C:\path\to\ws --mode live --scenario high-error-rate --url http://localhost:8001
```

```bash
./scripts/runbook-e2e.sh -w /path/to/workspace --mode live
```

Offline = `diag validate` + `diag lint`. Live = `diag e2e --url …` inside the
agent image (rewrites `localhost` → `host.docker.internal`).

Equivalent without the wrapper:

```bash
docker run --rm -v "$PWD/examples/hello-world:/workspace:ro" \
  ghcr.io/mskrado/diagnostic-agent:latest diag validate
docker compose exec diagnostic-agent diag e2e --url http://localhost:8000
```

---

## 5. Blind-eval cases

Offline / live blind evaluation cases live under [`eval/`](../eval/README.md)
(`blind_eval_dataset.yaml`). Install Testing section in generated `APPLY.md`
also documents `python -m app.cli eval …` (see [INSTALL.md](INSTALL.md)).

---

## 6. Host repo expectations

Hosts should:

1. Keep workspace `scenarios.yaml` + runbooks in *their* tree.
2. Either call these scripts from a sibling checkout of diagnostic-agent, or
   keep **thin wrappers** that forward args (container prefix, SSH target, `-w`).
3. Point operator docs at **this** `docs/TESTING.md` instead of duplicating recipes.

Example thin wrapper (host `scripts/diagnostic-agent-smoke-test.ps1`):

```powershell
$AgentRoot = Join-Path (Split-Path $PSScriptRoot -Parent) ".." "diagnostic-agent"
& (Join-Path $AgentRoot "scripts\smoke-test.ps1") -ContainerPrefix publishi @args
```
