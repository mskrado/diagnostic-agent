"""Console entrypoints for the diagnostic-agent package."""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="diagnostic-agent",
        description="Config-driven reactive diagnostic agent (Prometheus + Loki + LLM).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the FastAPI /alert webhook server")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")

    health = sub.add_parser("health-check", help="Print resolved profile + settings snapshot")

    args = parser.parse_args(argv)

    if args.command == "serve":
        import uvicorn

        uvicorn.run(
            "app.main:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
        return 0

    if args.command == "health-check":
        from app.config import settings
        from app.delivery.redact import active_rule_names
        from app.profile import get_profile

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

    return 1


if __name__ == "__main__":
    sys.exit(main())
