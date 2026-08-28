# Client fork model

Each deployment holds a **private copy** of this repository. Upstream ships product code; the client owns everything under `client/`. That separation keeps `git merge upstream/vX.Y.Z` conflict-free.

## Ownership contract

| Owner | Paths |
|-------|-------|
| **Upstream** (this repo) | `app/`, `runbooks/`, `examples/`, `eval/`, `tests/`, `docs/`, `scripts/`, `Dockerfile`, `requirements*.txt`, `pyproject.toml`, `.github/` |
| **Client** (your fork) | `client/**` — workspace, compose, `.env`, scripts, docs |

Upstream ships `client/` **empty** (only `README.md` + `.gitkeep`). Never edit upstream-owned files in your fork — put custom runbooks in `client/workspace/runbooks/`.

## 1. Create a private copy

GitHub **Fork** inherits public visibility. For a private deployment, mirror-clone instead:

```bash
git clone --bare https://github.com/mskrado/diagnostic-agent.git
cd diagnostic-agent.git
git push --mirror https://github.com/<your-org>/diagnostic-agent.git
cd ..
git clone https://github.com/<your-org>/diagnostic-agent.git
cd diagnostic-agent
git remote add upstream https://github.com/mskrado/diagnostic-agent.git
```

## 2. Initialize your deployment

From the repo root on the host where the agent will run:

```bash
pip install -e ".[dev]"
diag init
```

This discovers Prometheus/Loki/Grafana/Alertmanager (and optional Ollama), writes:

| Path | Purpose |
|------|---------|
| `client/workspace/` | Profiles, service map, runbooks, scenarios |
| `client/agent/` | Docker Compose, `.env`, build context |
| `client/observability/` | Prometheus/Alertmanager/Promtail snippets to merge |
| `client/scripts/` | `start.sh`, `stop.sh`, `status.sh`, `logs.sh`, `start.ps1` |
| `client/docs/OPERATIONS.md` | Seeded operational notes |
| `client/.upstream-version` | Upstream release marker |
| `client/.github/workflows/client-validate.yml` | CI for your workspace |

### Options

```bash
# Non-interactive against a known stack
diag init --accept-defaults --prometheus-url http://127.0.0.1:9090 ...

# Pull prebuilt GHCR image instead of building from source (online hosts)
diag init --pull-image --agent-image ghcr.io/mskrado/diagnostic-agent:1.1.4

# Internal PyPI mirror for air-gapped builds
diag init --pip-index-url https://pypi.internal/simple/
```

Default **`diag init` builds from source** (`diagnostic-agent:local`) so air-gapped and internal-mirror hosts never need GHCR.

## 3. Start the agent

```bash
cp client/agent/.env.example client/agent/.env   # first time only; fill secrets
./client/scripts/start.sh
# PowerShell: .\client\scripts\start.ps1
```

Standalone (no Docker): copy `client/systemd/diagnostic-agent.service` to `/etc/systemd/system/`, install `diag` on the host, enable the unit.

## 4. Customize

1. **`client/workspace/service_map.yaml`** — your topology (required for blast radius).
2. **`client/workspace/runbooks/`** — replace the reference corpus with your own.
3. **`client/workspace/prompt_profile.yaml`** — platform naming, golden commands ([playbook](PROMPT_PROFILE_AUTHORING.md)).
4. **`client/agent/.env`** — LLM provider, URLs, SMTP (never commit).

Merge `client/observability/` into your live Prometheus/Loki/Alertmanager configs.

## 5. Pull upstream updates

Before every upgrade, ensure you have not edited upstream paths:

```bash
diag doctor --check-fork
```

Merge a release:

```bash
git fetch upstream --tags
diag upgrade --target v1.2.0
# rebuild
./client/scripts/start.sh
```

`diag upgrade` refuses to proceed if upstream-owned files were modified locally. It updates `client/.upstream-version` and prints corpus diffs (`runbooks/`, presets) so you can port improvements into `client/workspace/runbooks/` deliberately.

### Offline (true air gap)

On a connected machine, build a pack per release:

```bash
./scripts/build-offline-pack.sh 1.2.0
# dist/offline-pack-1.2.0/{*.bundle,wheelhouse.tar.gz,base-image.tar}
```

Carry the directory to the isolated host, then:

```bash
docker load -i base-image.tar
diag upgrade --from-pack /path/to/offline-pack-1.2.0
./client/scripts/start.sh
```

## 6. Commit policy

Commit to **your** private repo:

- `client/workspace/` (except secrets)
- `client/agent/.env.example`, compose, Dockerfile
- `client/scripts/`, `client/docs/`
- Your own top-level docs

Never commit:

- `client/agent/.env`
- `client/**/install-report.json`

## 7. Reproducible builds

- **`requirements.lock`** — pinned deps; CI fails if it drifts from `requirements.txt`.
- **`BASE_IMAGE`** — pin Python base by digest in compose `build.args` for production.
- **`PIP_INDEX_URL`** — point builds at an internal PyPI proxy.

## Related

- [INSTALL.md](INSTALL.md) — `diag install` for throwaway bundles under `deploy/` (gitignored)
- [WORKSPACE.md](WORKSPACE.md) — workspace file reference
- [INTEGRATING.md](INTEGRATING.md) — Alertmanager wiring, verification
