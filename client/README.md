# Client deployment directory

This directory is **yours**. Upstream never writes here after the initial scaffold.

Run `diag init` from the repository root to generate your deployment:

```bash
diag init
```

That discovers your observability stack and writes:

| Path | Purpose |
|------|---------|
| `workspace/` | Profiles, service map, runbooks, scenarios |
| `agent/` | Docker Compose, `.env`, image build context |
| `scripts/` | `start`, `stop`, `status`, `logs` helpers |
| `docs/` | Your operational notes (seeded from discovery) |
| `.upstream-version` | Upstream release this copy was initialized from |

## Ownership contract

- **Upstream owns:** `app/`, `runbooks/`, `examples/`, `eval/`, `tests/`, `docs/`, `scripts/`, `Dockerfile`, `requirements*.txt`, `pyproject.toml`, `.github/`
- **You own:** everything under `client/**`

Never edit upstream-owned paths in your fork. Custom runbooks belong in `client/workspace/runbooks/`. Pull upstream updates with `diag upgrade` (see [docs/CLIENT_FORK.md](../docs/CLIENT_FORK.md)).

## Secrets

`client/agent/.env` is gitignored. Commit `client/agent/.env.example` only.
