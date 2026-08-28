"""Verify a generated install bundle."""
from __future__ import annotations

import subprocess
from pathlib import Path

import yaml


def verify(output: Path, *, allow_degraded: bool = False, layout: str = "bundle") -> list[str]:
    """Return a list of error strings (empty = OK).

    Default verifies a **complete** bundle (agent build/run + observability
    wiring for the reactive path). Pass ``allow_degraded=True`` when the
    bundle was generated with ``--allow-degraded``.
    """
    errors: list[str] = []
    output = output.resolve()
    client_layout = layout == "client"
    workspace = (
        output / "workspace"
        if client_layout
        else output / "agent" / "workspace"
    )
    env_file = output / "agent" / ".env"
    rules = output / "observability" / "prometheus" / "alert-rules.generated.yml"
    am_route = output / "observability" / "alertmanager" / "route.generated.yml"
    apply_md = output / "APPLY.md"
    report = output / "install-report.json"

    for required in (
        workspace / "agent.yaml",
        workspace / "blind_eval.yaml",
        workspace / "scenarios.yaml",
        env_file,
        rules,
        apply_md,
        report,
    ):
        if not required.is_file():
            errors.append(f"missing required file: {required}")

    runbooks = workspace / "runbooks"
    if not runbooks.is_dir() or not any(runbooks.glob("runbook-*.md")):
        errors.append("workspace/runbooks has no runbook-*.md files")

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

    env_map = _parse_env(env_file) if env_file.is_file() else {}
    if not env_map.get("AGENT_PROMETHEUS_URL"):
        errors.append("agent/.env missing AGENT_PROMETHEUS_URL")

    if allow_degraded:
        # Degraded bundles may omit Loki and/or Alertmanager webhook wiring.
        pass
    else:
        if not env_map.get("AGENT_LOKI_URL"):
            errors.append(
                "agent/.env missing AGENT_LOKI_URL "
                "(complete install requires Loki; use --allow-degraded otherwise)"
            )
        if not am_route.is_file():
            errors.append(
                "missing observability/alertmanager/route.generated.yml "
                "(complete install requires Alertmanager webhook wiring; "
                "use --allow-degraded otherwise)"
            )
        elif am_route.is_file():
            try:
                doc = yaml.safe_load(am_route.read_text(encoding="utf-8")) or {}
                receivers = doc.get("receivers") or []
                if not receivers:
                    errors.append("alertmanager route.generated.yml has no receivers")
            except yaml.YAMLError as exc:
                errors.append(f"alertmanager route YAML error: {exc}")

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
            if ws.blind_eval_path is None:
                errors.append("workspace did not resolve blind_eval.yaml")
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


def _parse_env(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser for generated agent/.env files."""
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def shutil_which(cmd: str) -> str | None:
    from shutil import which

    return which(cmd)
