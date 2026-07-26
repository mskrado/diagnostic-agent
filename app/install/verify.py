"""Verify a generated install bundle."""
from __future__ import annotations

import subprocess
from pathlib import Path

import yaml


def verify(output: Path) -> list[str]:
    """Return a list of error strings (empty = OK)."""
    errors: list[str] = []
    output = output.resolve()
    workspace = output / "agent" / "workspace"
    env_file = output / "agent" / ".env"
    rules = output / "observability" / "prometheus" / "alert-rules.generated.yml"
    apply_md = output / "APPLY.md"
    report = output / "install-report.json"

    for required in (workspace / "agent.yaml", env_file, rules, apply_md, report):
        if not required.is_file():
            errors.append(f"missing required file: {required}")

    if (workspace / "redaction.yaml").is_file():
        raw = yaml.safe_load(
            (workspace / "redaction.yaml").read_text(encoding="utf-8")
        ) or {}
        if not raw.get("extends") and not raw.get("rules"):
            errors.append("redaction.yaml has neither extends nor rules")
    else:
        errors.append("missing redaction.yaml (AGENT_REQUIRE_REDACTION hard gate)")

    if rules.is_file():
        try:
            doc = yaml.safe_load(rules.read_text(encoding="utf-8")) or {}
            groups = doc.get("groups") or []
            if not groups or not groups[0].get("rules"):
                errors.append("alert-rules.generated.yml has no rules")
        except yaml.YAMLError as exc:
            errors.append(f"alert-rules YAML error: {exc}")

    # Prefer in-process validate when the package is importable.
    if workspace.is_dir() and (workspace / "agent.yaml").is_file():
        try:
            from app.workspace import load as load_workspace

            ws = load_workspace(str(workspace))
            profile = ws.profile()
            if not profile.redaction.rules:
                errors.append(
                    "resolved profile has 0 redaction rules -- "
                    "reports would carry unredacted data"
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"workspace validate: {exc}")

    # Optional external linters when present on PATH.
    if rules.is_file() and shutil_which("promtool"):
        proc = subprocess.run(
            ["promtool", "check", "rules", str(rules)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            errors.append(f"promtool: {proc.stderr.strip() or proc.stdout.strip()}")

    return errors


def shutil_which(cmd: str) -> str | None:
    from shutil import which

    return which(cmd)
