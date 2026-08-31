# Dependencies — moved

Dependency layout, host bootstrap, and lock-file policy now live in
**[INSTALL.md § 2 — Get the `diag` CLI](INSTALL.md#2-get-the-diag-cli)**.

| You were looking for | Now at |
|---|---|
| `requirements.txt` / `.lock` / `pyproject.toml` roles | [Dependency files](INSTALL.md#dependency-files) |
| One-command host install (`install-system-deps.sh`, `bootstrap-venv.sh`) | [Option A — host Python](INSTALL.md#option-a--host-python-machine-already-has-311) |
| One-shot Docker CLI (`diag init` / `scan` / `draft` when host Python is unusable) | [Option B — one-shot Docker](INSTALL.md#option-b--one-shot-docker-no-usable-host-python-typical-amazon-linux-2) |
| Docker / air-gapped builds | [Air-gapped installs and upgrades](INSTALL.md#9-air-gapped-installs-and-upgrades) |
| Regenerating the lock | [Dependency files](INSTALL.md#dependency-files) |
