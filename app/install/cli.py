"""CLI entry for ``diag install``."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .collect import collect
from .discover import discover
from .generate import generate
from .models import DiscoveryReport
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
        "--yes",
        "-y",
        action="store_true",
        help="Confirm destructive apply actions without prompting",
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
    print(f"diag install - target={args.target} output={args.output}")
    report: DiscoveryReport = discover(
        target=args.target, ssh=args.ssh, timeout=args.timeout
    )

    _print_discovery(report)

    if report.errors and not args.prometheus_url:
        for err in report.errors:
            print(f"ERROR: {err}", file=sys.stderr)
        if args.non_interactive:
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
            non_interactive=args.non_interactive,
            allow_degraded=args.allow_degraded,
            overrides=overrides,
        )
    except ValueError as exc:
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
    print(f"Next: read {output / 'APPLY.md'}")

    if args.apply:
        from .apply import apply_reloads

        if not args.yes and not args.non_interactive:
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
    print("\nDiscovery")
    print("---------")
    for tool in report.tools:
        mark = "OK " if tool.reachable else " - "
        extra = tool.url or "(not found)"
        print(f"  [{mark}] {tool.kind.value:<14} {extra}")
    for w in report.warnings:
        print(f"  ! {w}")
