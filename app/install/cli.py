"""CLI entry for ``diag install``."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .collect import collect
from .discover import discover
from .generate import generate
from .models import DiscoveryReport
from .progress import make_progress
from .prompt import PromptAborted
from .verify import verify


def add_install_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "install",
        help=(
            "Discover observability tools on a host and generate agent + "
            "observability config into --output"
        ),
    )
    p.add_argument(
        "--target",
        default="local",
        help="local (default) or host/base URL to probe",
    )
    p.add_argument(
        "--output",
        "-o",
        required=True,
        metavar="DIR",
        help="Directory to write the install bundle into",
    )
    p.add_argument(
        "--ssh",
        default=None,
        metavar="USER@HOST",
        help="Optional SSH target for remote docker/systemd introspection",
    )
    p.add_argument(
        "--preset",
        default="auto",
        choices=["auto", "generic-prometheus", "spring-micrometer"],
        help="Metrics/logs preset (default: auto-detect)",
    )
    p.add_argument(
        "--prometheus-url",
        default=None,
        help="Override discovered Prometheus URL",
    )
    p.add_argument("--loki-url", default=None, help="Override Loki URL")
    p.add_argument("--grafana-url", default=None, help="Override Grafana URL")
    p.add_argument(
        "--alertmanager-url",
        default=None,
        help="Override Alertmanager URL",
    )
    p.add_argument(
        "--webhook-url",
        default=None,
        help="Override Alertmanager -> agent webhook URL",
    )
    p.add_argument(
        "--chat-provider",
        default=None,
        help="Force LLM provider (ollama|openai|bedrock_converse|anthropic|google_genai)",
    )
    p.add_argument("--chat-model", default=None)
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan without writing files",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files (still keeps timestamped backups)",
    )
    p.add_argument(
        "--non-interactive",
        action="store_true",
        help="Never prompt; require flags/env for missing secrets",
    )
    p.add_argument(
        "--allow-degraded",
        action="store_true",
        help=(
            "Permit soft-degrade (metrics-only without Loki, no Alertmanager "
            "webhook route, blind Ollama LLM fallback). Default is fail closed."
        ),
    )
    p.add_argument(
        "--accept-defaults",
        action="store_true",
        help=(
            "Resolve interactively but accept every discovered default without "
            "prompting (fast re-run against a known stack)"
        ),
    )
    p.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the review confirmation and any --apply prompt",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Best-effort reload Prometheus/Alertmanager after generating",
    )
    p.add_argument(
        "--start",
        action="store_true",
        help="docker compose up the generated agent service",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help="HTTP probe timeout seconds (default: 3)",
    )
    p.set_defaults(func=run_install)


def run_install(args: argparse.Namespace) -> int:
    try:
        return _run_install(args)
    except KeyboardInterrupt:
        print("\nAborted by operator; nothing was written.", file=sys.stderr)
        return 130


def _run_install(args: argparse.Namespace) -> int:
    print(f"diag install - target={args.target} output={args.output}")

    non_interactive = args.non_interactive
    if not non_interactive and not args.accept_defaults and not sys.stdin.isatty():
        print(
            "stdin is not a terminal -- switching to non-interactive mode "
            "(pass --accept-defaults to resolve from discovery instead)."
        )
        non_interactive = True

    progress = make_progress(args.target)
    report: DiscoveryReport = discover(
        target=args.target,
        ssh=args.ssh,
        timeout=args.timeout,
        progress=progress,
    )

    # Live chart already left the final summary on screen when TTY; otherwise
    # print the static discovery block that scripts/CI rely on.
    if not getattr(progress, "enabled", False):
        _print_discovery(report)
    else:
        for w in report.warnings:
            print(f"  ! {w}")
        print()

    if report.errors and not args.prometheus_url:
        for err in report.errors:
            print(f"ERROR: {err}", file=sys.stderr)
        if non_interactive:
            print(
                "\nDiscovery failed. Fix connectivity or pass --prometheus-url.",
                file=sys.stderr,
            )
            return 1
        print(
            "\nDiscovery incomplete — continuing to collect required "
            "parameters interactively.",
            file=sys.stderr,
        )

    overrides = {
        "preset": args.preset,
        "prometheus_url": args.prometheus_url,
        "loki_url": args.loki_url,
        "grafana_url": args.grafana_url,
        "alertmanager_url": args.alertmanager_url,
        "webhook_url": args.webhook_url,
        "chat_provider": args.chat_provider,
        "chat_model": args.chat_model,
    }
    try:
        params = collect(
            report,
            preset=args.preset,
            non_interactive=non_interactive,
            allow_degraded=args.allow_degraded,
            accept_defaults=args.accept_defaults,
            assume_yes=args.yes,
            probe_timeout=args.timeout,
            overrides=overrides,
        )
    except (ValueError, PromptAborted) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    output = Path(args.output)
    written = generate(
        output=output,
        report=report,
        params=params,
        dry_run=args.dry_run,
        force=args.force,
    )
    print(f"\n{'Would write' if args.dry_run else 'Wrote'} {len(written)} file(s)")
    for decision in report.decisions:
        print(f"  - {decision}")

    if args.dry_run:
        return 0

    errors = verify(output, allow_degraded=args.allow_degraded)
    if errors:
        print("\nverify FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("\nverify OK")
    _print_next_steps(output, params, report)

    if args.apply:
        from .apply import apply_reloads

        if not args.yes and not non_interactive:
            confirm = input(
                "Reload Prometheus/Alertmanager configs on the live stack? [y/N]: "
            )
            if confirm.strip().lower() not in ("y", "yes"):
                print("Skipping --apply")
            else:
                for note in apply_reloads(params):
                    print(f"apply: {note}")
        else:
            for note in apply_reloads(params):
                print(f"apply: {note}")

    if args.start:
        from .apply import health_check, start_agent

        rc, msg = start_agent(output)
        print(f"start: {msg}")
        if rc != 0:
            return rc
        ok, health_msg = health_check(params)
        print(f"start: {health_msg}")
        if not ok:
            return 1

    return 0


def _print_discovery(report: DiscoveryReport) -> None:
    tools = sorted(report.tools, key=lambda t: (not t.reachable, t.kind.value))
    found = sum(1 for t in tools if t.reachable)
    header = f"Discovery ({found}/{len(tools)} reachable on {report.target})"
    print(f"\n{header}")
    print("-" * len(header))
    for tool in tools:
        mark = "OK " if tool.reachable else " - "
        detail = tool.url or "(not found)"
        if tool.reachable and tool.version:
            detail = f"{detail}  v{tool.version}"
        print(f"  [{mark}] {tool.kind.value:<14} {detail}")
    for w in report.warnings:
        print(f"  ! {w}")
    print(f"  placement: {report.reachability.agent_placement}")


def _print_next_steps(
    output: Path, params: object, report: DiscoveryReport
) -> None:
    """Print copy-pasteable commands instead of only pointing at APPLY.md."""
    print("\nNext steps")
    print("----------")
    print(f"  1. Review   {output / 'install-report.json'}")
    print(f"  2. Edit     {output / 'agent' / 'workspace' / 'service_map.yaml'}")
    print(f"  3. Start    cd {output / 'agent'} && docker compose --env-file .env up -d")
    print(
        f"  4. Health   curl -sf http://127.0.0.1:"
        f"{getattr(params, 'agent_host_port', 8001)}/health"
    )
    print(f"  5. Wire     merge {output / 'observability'} into your live stack")
    print(f"\nFull instructions: {output / 'APPLY.md'}")
    if report.warnings:
        print("\nWarnings to resolve before the agent can diagnose:")
        for w in report.warnings:
            print(f"  ! {w}")
