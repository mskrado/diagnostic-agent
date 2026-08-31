"""Console entrypoints for the diagnostic-agent package.

Every command operates on a host workspace (see :mod:`app.workspace`), so a
host project supplies configuration and content and never passes paths::

    docker run --rm -v "$PWD/infrastructure/diagnostic-agent:/workspace" \\
        ghcr.io/mskrado/diagnostic-agent:<tag> diag validate
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path

import yaml

from .workspace import Workspace, WorkspaceError
from .workspace import load as load_workspace


def _add_workspace_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-w",
        "--workspace",
        default=None,
        metavar="PATH",
        help=(
            "Workspace directory (default: $AGENT_WORKSPACE, else the nearest "
            "enclosing agent.yaml, else the working directory)"
        ),
    )


def _apply_workspace_env(ws: Workspace) -> None:
    """Feed the workspace into Settings for commands that run the agent.

    Explicit non-empty environment variables still win (precedence documented in
    :mod:`app.profile.loader`). Empty strings are treated as unset: the published
    image historically shipped ``AGENT_PROFILE_DIR=""``, and ``setdefault`` would
    leave that empty value in place — shadowing the mounted workspace profile.
    """
    def _blank(name: str) -> bool:
        return not (os.environ.get(name) or "").strip()

    if ws.profile_dir and _blank("AGENT_PROFILE_DIR"):
        os.environ["AGENT_PROFILE_DIR"] = str(ws.profile_dir)
    if ws.runbooks_dir and _blank("AGENT_RUNBOOKS_PATH"):
        os.environ["AGENT_RUNBOOKS_PATH"] = str(ws.runbooks_dir)
    if _blank("AGENT_DEFAULT_PRESET"):
        os.environ["AGENT_DEFAULT_PRESET"] = ws.preset


def _package_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("diagnostic-agent")
    except PackageNotFoundError:
        return "unknown"


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    _apply_workspace_env(load_workspace(args.workspace))
    uvicorn.run(
        "app.main:app", host=args.host, port=args.port, reload=args.reload
    )
    return 0


def _cmd_health_check(args: argparse.Namespace) -> int:
    ws = load_workspace(args.workspace)
    _apply_workspace_env(ws)

    from app import config as config_mod
    from app.delivery.redact import active_rule_names
    from app.profile import get_profile

    config_mod.settings = config_mod.Settings()
    settings = config_mod.settings
    profile = get_profile()
    rules = active_rule_names()
    print(f"profile={profile.name}")
    print(f"preset={settings.default_preset}")
    print(f"profile_dir={settings.profile_dir or '(none)'}")
    print(f"service_map={settings.resolved_service_map_path() or '(none)'}")
    print(f"runbooks={settings.resolved_runbooks_path()}")
    print(f"redaction={len(rules)} rules {list(rules)}")
    print(f"models={settings.model_snapshot()}")
    if not rules:
        print(
            "ERROR: 0 redaction rules — reports would carry unredacted data. "
            "The server refuses to start unless AGENT_REQUIRE_REDACTION=false.",
            file=sys.stderr,
        )
        return 1
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    ws = load_workspace(args.workspace)
    errors: list[str] = []

    print(f"agent {_package_version()}")
    for key, value in ws.describe().items():
        print(f"{key}={value}")
    for warning in ws.warnings:
        print(f"WARNING: {warning}")

    if ws.agent_version and ws.agent_version != _package_version():
        print(
            f"WARNING: workspace pins agent_version {ws.agent_version} but this "
            f"agent is {_package_version()}"
        )

    profile = ws.profile()
    if profile.load_errors:
        errors.extend(profile.load_errors)
    rules = profile.redaction.rules
    print(f"redaction={len(rules)} rules {[r.name for r in rules]}")
    if not rules:
        errors.append(
            "0 redaction rules — reports would carry unredacted data. Add rules "
            "to redaction.yaml (they are additive on top of the preset)."
        )

    if profile.service_map_path:
        try:
            with open(profile.service_map_path, encoding="utf-8") as f:
                yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as exc:
            errors.append(f"service_map: {exc}")
        else:
            from app.dependency_map import DependencyMap

            services = DependencyMap.load(profile.service_map_path).known_services()
            print(f"services={len(services)}")
    else:
        print("services=0 (no service_map.yaml; blast radius will be empty)")

    if errors:
        print("\nvalidate FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("\nvalidate OK")
    return 0


def _cmd_lint(args: argparse.Namespace) -> int:
    from .tools.corpus_lint import lint

    ws = load_workspace(args.workspace)
    result = lint(ws)
    for note in result.notes:
        print(f"note: {note}")
    if result.errors:
        print("corpus lint FAILED:", file=sys.stderr)
        for e in result.errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("corpus lint OK")
    return 0


def _probe_http(name: str, url: str) -> tuple[bool, str]:
    import httpx

    try:
        resp = httpx.get(url, timeout=5.0)
    except Exception as exc:  # noqa: BLE001 - report any failure verbatim
        return False, f"{name}: {type(exc).__name__}: {exc}"
    ok = resp.status_code < 400
    return ok, f"{name}: HTTP {resp.status_code} {url}"


def _cmd_doctor(args: argparse.Namespace) -> int:
    if getattr(args, "check_fork", False):
        from app.fork.drift import (
            DriftCheckError,
            find_upstream_drift,
            read_upstream_version,
        )
        from app.fork.boundary import CLIENT_DIR

        repo_root = Path(__file__).resolve().parent.parent
        try:
            drift = find_upstream_drift(repo_root)
        except DriftCheckError as exc:
            print(f"FAIL could not verify fork drift: {exc}", file=sys.stderr)
            return 1
        version = read_upstream_version(repo_root / CLIENT_DIR)
        print(f"upstream_version={version or '(none)'}")
        if drift:
            print("FAIL upstream drift detected in:")
            for path in drift:
                print(f"  {path}")
            print(
                "\nRevert edits to upstream-owned paths. Client config belongs "
                f"under {CLIENT_DIR}/.",
                file=sys.stderr,
            )
            return 1
        print("fork hygiene OK (no upstream-owned drift)")
        if not args.workspace and not os.environ.get("AGENT_WORKSPACE"):
            return 0

    ws = load_workspace(args.workspace)
    _apply_workspace_env(ws)

    from app import config as config_mod

    config_mod.settings = config_mod.Settings()
    settings = config_mod.settings

    checks = [
        _probe_http("prometheus", f"{settings.prometheus_url}/-/ready"),
        _probe_http("loki", f"{settings.loki_url}/ready"),
    ]
    if settings.grafana_token:
        checks.append(_probe_http("grafana", f"{settings.grafana_url}/api/health"))
    else:
        checks.append((True, "grafana: skipped (no AGENT_GRAFANA_TOKEN)"))

    if settings.email_enabled:
        try:
            with socket.create_connection(
                (settings.smtp_host, settings.smtp_port), timeout=5.0
            ):
                checks.append(
                    (True, f"smtp: reachable {settings.smtp_host}:{settings.smtp_port}")
                )
        except OSError as exc:
            checks.append(
                (False, f"smtp: {settings.smtp_host}:{settings.smtp_port}: {exc}")
            )
    else:
        checks.append((True, "smtp: skipped (email disabled)"))

    failed = 0
    for ok, message in checks:
        print(f"{'ok  ' if ok else 'FAIL'} {message}")
        failed += 0 if ok else 1
    print(f"models={settings.model_snapshot()}")

    if failed:
        print(f"\ndoctor FAILED: {failed} check(s)", file=sys.stderr)
        return 1
    print("\ndoctor OK")
    return 0


def _cmd_e2e(args: argparse.Namespace) -> int:
    import httpx

    from .tools.scenarios import load_scenarios

    ws = load_workspace(args.workspace)
    scenarios = load_scenarios(ws)
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        scenarios = [s for s in scenarios if s["id"] in wanted]
        missing = wanted - {s["id"] for s in scenarios}
        if missing:
            print(f"ERROR: unknown scenario id(s): {sorted(missing)}", file=sys.stderr)
            return 2

    base = args.url.rstrip("/")
    failures: list[str] = []
    try:
        with httpx.Client(timeout=args.timeout) as client:
            health = client.get(f"{base}/health")
            health.raise_for_status()
            if health.json().get("agent_initialized") is not True:
                print(f"ERROR: agent at {base} is not initialized", file=sys.stderr)
                return 1

            failures = _run_scenarios(client, base, scenarios)
    except httpx.HTTPError as exc:
        print(f"ERROR: cannot reach agent at {base}: {exc}", file=sys.stderr)
        return 2

    if failures:
        print(
            f"\ne2e FAILED: {len(failures)}/{len(scenarios)} scenario(s)",
            file=sys.stderr,
        )
        return 1
    print(f"\ne2e OK: {len(scenarios)} scenario(s)")
    return 0


def _run_scenarios(client, base: str, scenarios: list[dict]) -> list[str]:
    """POST each scenario and collect human-readable failures."""
    from .tools.scenarios import build_alertmanager_payload

    failures: list[str] = []
    for scenario in scenarios:
        sid = scenario["id"]
        labels = scenario["labels"]
        resp = client.post(f"{base}/alert", json=build_alertmanager_payload(scenario))
        if resp.status_code >= 400:
            failures.append(f"{sid}: HTTP {resp.status_code}")
            print(f"FAIL {sid}: HTTP {resp.status_code}")
            continue

        body = resp.json()
        problems: list[str] = []
        if body.get("count", 0) < 1:
            problems.append("no reports returned")
        else:
            report = body["reports"][0]
            for field, expected in (
                ("service", labels["service"]),
                ("alert_type", labels["alertname"]),
                ("severity", labels["severity"]),
            ):
                if report.get(field) != expected:
                    problems.append(
                        f"{field}={report.get(field)!r} expected {expected!r}"
                    )
            for field in ("diagnosis", "evidence"):
                if field not in report:
                    problems.append(f"missing {field}")

        # The payload seeds these; leaking them means redaction is off.
        raw = resp.text.lower()
        for probe in ("tenant-smoke-test", "550e8400-e29b-41d4-a716-446655440000"):
            if probe in raw:
                problems.append(f"redaction leak: {probe!r} in response")

        if problems:
            failures.append(f"{sid}: {'; '.join(problems)}")
            print(f"FAIL {sid}: {'; '.join(problems)}")
        else:
            print(f"ok   {sid}")

    return failures


def _cmd_eval(args: argparse.Namespace) -> int:
    from .tools import blind_eval

    ws = load_workspace(args.workspace)
    _apply_workspace_env(ws)
    return blind_eval.main(args.args, workspace=ws)


def _cmd_replay(args: argparse.Namespace) -> int:
    from .tools import replay_eval

    ws = load_workspace(args.workspace)
    _apply_workspace_env(ws)
    forwarded: list[str] = []
    if args.dataset:
        forwarded.extend(["--dataset", args.dataset])
    if args.out:
        forwarded.extend(["--out", args.out])
    if args.only:
        forwarded.extend(["--only", args.only])
    return replay_eval.main(forwarded, workspace=ws)


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="diag",
        description=(
            "Config-driven reactive diagnostic agent (Prometheus + Loki + LLM). "
            "Commands operate on a host workspace; see docs/WORKSPACE.md."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the FastAPI /alert webhook server")
    _add_workspace_arg(serve)
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=_cmd_serve)

    health = sub.add_parser(
        "health-check", help="Print the resolved profile + settings snapshot"
    )
    _add_workspace_arg(health)
    health.set_defaults(func=_cmd_health_check)

    validate = sub.add_parser(
        "validate", help="Check the workspace manifest, profile, and redaction rules"
    )
    _add_workspace_arg(validate)
    validate.set_defaults(func=_cmd_validate)

    lint = sub.add_parser(
        "lint", help="Corpus lint: runbook/scenario coverage and blind-eval grounding"
    )
    _add_workspace_arg(lint)
    lint.set_defaults(func=_cmd_lint)

    doctor = sub.add_parser(
        "doctor", help="Probe Prometheus, Loki, Grafana, and SMTP connectivity"
    )
    _add_workspace_arg(doctor)
    doctor.add_argument(
        "--check-fork",
        action="store_true",
        help="Fail if upstream-owned paths were modified (client fork hygiene)",
    )
    doctor.set_defaults(func=_cmd_doctor)

    e2e = sub.add_parser(
        "e2e", help="POST each scenario at a running agent and assert its report"
    )
    _add_workspace_arg(e2e)
    e2e.add_argument("--url", required=True, help="Base URL of a running agent")
    e2e.add_argument("--timeout", type=float, default=300.0)
    e2e.add_argument("--only", default="", metavar="IDS", help="Comma-separated ids")
    e2e.set_defaults(func=_cmd_e2e)

    evaluate = sub.add_parser("eval", help="Run an evaluation over the workspace")
    _add_workspace_arg(evaluate)
    evaluate.add_argument("kind", choices=["blind"])
    evaluate.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to the evaluator (--help for the full list)",
    )
    evaluate.set_defaults(func=_cmd_eval)

    replay = sub.add_parser(
        "replay",
        help="Replay workspace alert scenarios and assert routing/runbook expectations",
    )
    _add_workspace_arg(replay)
    replay.add_argument(
        "--dataset",
        default="",
        metavar="PATH",
        help="Replay dataset YAML (default: workspace scenarios file)",
    )
    replay.add_argument(
        "--out",
        default="",
        metavar="DIR",
        help="Directory for replay result JSON files",
    )
    replay.add_argument(
        "--only",
        default="",
        metavar="IDS",
        help="Comma-separated scenario id(s) to replay",
    )
    replay.set_defaults(func=_cmd_replay)

    from .install.cli import add_install_parser
    from .install.init_cli import add_init_parser
    from .fork.upgrade_cli import add_upgrade_parser
    from .draft.cli import add_draft_parser
    from .scan.cli import add_scan_parser
    from .mine_eval_cli import add_mine_eval_parser
    from .drift.cli import add_drift_parser

    add_install_parser(sub)
    add_init_parser(sub)
    add_upgrade_parser(sub)
    add_scan_parser(sub)
    add_draft_parser(sub)
    add_mine_eval_parser(sub)
    add_drift_parser(sub)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except WorkspaceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
