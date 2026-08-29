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
# Replace YOUR_ORG with your GitHub org/user — do not leave angle brackets in the URL
git push --mirror https://github.com/YOUR_ORG/diagnostic-agent.git
cd ..
git clone https://github.com/YOUR_ORG/diagnostic-agent.git
cd diagnostic-agent
git remote add upstream https://github.com/mskrado/diagnostic-agent.git
```

## 2. Initialize your deployment

You need the `diag` CLI once to scaffold `client/`. Runtime afterward is Docker
Compose (or systemd); you do **not** need a permanent system-wide `pip install`.

### Host Python (recommended when the box has Python ≥3.11)

Dependency layout and lock-file rules: **[DEPENDENCIES.md](DEPENDENCIES.md)**.

```bash
./scripts/install-system-deps.sh   # yum/dnf/apt packages (AL2, AL2023, Ubuntu)
# Amazon Linux 2 only: follow the script's pyenv + openssl11 instructions, then:
./scripts/bootstrap-venv.sh        # creates .venv from requirements.lock
source .venv/bin/activate
diag init
```

### No usable host Python (typical Amazon Linux 2)

Run init in a one-shot container; files land on the host via the bind mount:

```bash
docker run --rm \
  -v "$PWD:/work" -w /work \
  --network host \
  python:3.12-slim \
  bash -c 'pip install -q -e . && diag init --accept-defaults --allow-degraded'
```

Do **not** paste angle-bracket placeholders into the shell. In the mirror-clone
example above, replace `<your-org>` with your real GitHub org name (or bash
treats `<` / `>` as redirection).

This discovers Prometheus/Loki/Grafana/Alertmanager (and optional Ollama), writes:

| Path | Purpose |
|------|---------|
| `client/workspace/` | Profiles, service map, runbooks, scenarios |
| `client/agent/` | Docker Compose, `.env`, build context |
| `client/observability/` | Prometheus/Alertmanager/Promtail snippets to merge |
| `client/scripts/` | `start.sh`, `stop.sh`, `status.sh`, `logs.sh`, `start.ps1` |
| `client/systemd/` | `diagnostic-agent.service` for standalone (non-Docker) runs |
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

The generated build uses the **repo root as its Docker build context** (`context: ../..`, `dockerfile: client/agent/Dockerfile`), because the image is built from `app/`, `runbooks/` and `requirements.lock`. Deploy the whole fork to the host — copying only `client/` gives you a compose file that cannot build. Use `--pull-image` if you would rather ship just the workspace and pull a prebuilt image.

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

`diag upgrade` refuses to proceed if upstream-owned files were modified locally. It updates `client/.upstream-version` and prints corpus diffs (`runbooks/`, presets) so you can port improvements into `client/workspace/runbooks/` deliberately. Use `--skip-drift-check` only when you have accepted the conflicts you are about to get.

The drift check compares your working tree against `HEAD`, so it catches edits you have not committed. If you deliberately commit a patch to an upstream path, the check stays quiet and `git merge` reports the conflict instead — expected, but it is why carrying local patches is discouraged. Upstream fixes belong in a PR to this repo; everything host-specific belongs under `client/`.

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
- **`BASE_IMAGE`** — the Python base image. Pin it by digest for production.
- **`PIP_INDEX_URL`** / **`PIP_EXTRA_INDEX_URL`** — point builds at an internal PyPI proxy.

The generated compose reads all three from `client/agent/.env`, so set them there rather than editing the compose file — that survives a re-run of `diag init`:

```bash
# client/agent/.env
BASE_IMAGE=python:3.12-slim@sha256:<digest>
PIP_INDEX_URL=https://pypi.internal/simple/
```

Empty or unset index URLs mean "use the public PyPI index".

## Related

- [INSTALL.md](INSTALL.md) — `diag install` for throwaway bundles under `deploy/` (gitignored)
- [WORKSPACE.md](WORKSPACE.md) — workspace file reference
- [INTEGRATING.md](INTEGRATING.md) — Alertmanager wiring, verification
