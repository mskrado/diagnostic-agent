# Dependencies

How this project declares and installs Python packages.

## Files (do not duplicate lists by hand)

| File | Role |
|------|------|
| **`requirements.txt`** | Runtime dependency *ranges* — source of truth for packaging and Docker when no lock is used |
| **`requirements.lock`** | Exact pins produced by `pip-compile` from `requirements.txt` — use this for reproducible host/CI/image installs |
| **`requirements-dev.txt`** | Test/dev-only packages (`pytest`, …), exposed as the `dev` extra |
| **`pyproject.toml`** | Package metadata, `requires-python = ">=3.11"`, console scripts; reads the requirements files via setuptools dynamic deps so `pip install .` and `pip install -r` cannot drift |
| **`.python-version`** | Hint for pyenv / asdf (`3.12`) |
| **`deps/*.txt`** | OS packages (yum/apt) needed to *build* Python or compile wheels on the host |

```text
pyproject.toml  ──reads──►  requirements.txt  ──pip-compile──►  requirements.lock
                    └──reads──►  requirements-dev.txt  (optional extra: .[dev])
```

## One-command host install

```bash
# 1) OS packages (Amazon Linux 2 / 2023 / Ubuntu)
./scripts/install-system-deps.sh

# 2) On Amazon Linux 2 only: install Python 3.12 with pyenv *after* openssl11
#    (see script output). Skip on AL2023 / Ubuntu if python3.12 is already present.

# 3) Venv + locked deps + `diag` console script
./scripts/bootstrap-venv.sh          # runtime only
./scripts/bootstrap-venv.sh --dev    # + pytest tooling

source .venv/bin/activate
diag init
```

`bootstrap-venv.sh` refuses interpreters older than 3.11 and installs from
`requirements.lock` so every host gets the same package versions as CI/Docker.

## Docker / air-gapped builds

The `Dockerfile` prefers `requirements.lock` when present. Offline packs ship a
wheelhouse built from the same lock (`scripts/build-offline-pack.sh`).

## Regenerating the lock

After changing `requirements.txt` (CI Python 3.12):

```bash
pip install pip-tools
pip-compile requirements.txt -o requirements.lock --strip-extras
```

CI fails if the committed lock no longer matches a seeded recompile of
`requirements.txt` (see `.github/workflows/ci.yml`).
