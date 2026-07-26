"""Resolve every parameter required for a self-sufficient install."""
from __future__ import annotations

import getpass
import os
from typing import Any

from .models import DiscoveryReport, InstallParams, ToolKind


def collect(
    report: DiscoveryReport,
    *,
    preset: str = "auto",
    non_interactive: bool = False,
    allow_degraded: bool = False,
    overrides: dict[str, Any] | None = None,
) -> InstallParams:
    """Merge discovery + env/flags + prompts into :class:`InstallParams`.

    Default is **fail closed**: Prometheus, Loki, Alertmanager (+ webhook), and a
    usable LLM must be resolved before a complete install bundle is emitted.
    Soft-degrade (metrics-only / no webhook route / blind Ollama fallback) requires
    ``allow_degraded=True`` (``--allow-degraded``). Grafana annotations and SMTP
    remain optional delivery channels.
    """
    overrides = overrides or {}
    params = InstallParams()
    matrix = report.reachability
    missing: list[str] = []

    # --- Endpoints from reachability matrix ---
    params.prometheus_url = _first(
        overrides.get("prometheus_url"),
        matrix.agent_to_prometheus,
        os.environ.get("AGENT_PROMETHEUS_URL"),
    )
    params.loki_url = _first(
        overrides.get("loki_url"),
        matrix.agent_to_loki,
        os.environ.get("AGENT_LOKI_URL"),
    )
    params.grafana_url = _first(
        overrides.get("grafana_url"),
        matrix.agent_to_grafana,
        os.environ.get("AGENT_GRAFANA_URL"),
    )
    params.alertmanager_url = _first(
        overrides.get("alertmanager_url"),
        matrix.agent_to_alertmanager,
    )
    params.webhook_url = _first(
        overrides.get("webhook_url"),
        matrix.alertmanager_to_agent_webhook,
        "http://diagnostic-agent:8000/webhook",
    )
    params.docker_network = matrix.shared_docker_network
    params.agent_host_port = matrix.agent_host_port
    params.agent_container_name = matrix.agent_container_name

    # --- Preset ---
    params.preset = _resolve_preset(preset, report, overrides)

    # --- Required data / control plane (prompt, then fail closed unless degraded) ---
    if not params.prometheus_url and not non_interactive:
        params.prometheus_url = _prompt(
            "Prometheus URL",
            default="http://127.0.0.1:9090",
        )
        if params.prometheus_url:
            report.decisions.append(
                f"Prometheus URL from prompt -> {params.prometheus_url}"
            )

    if not params.loki_url:
        if allow_degraded:
            params.metrics_only = True
            report.decisions.append("Loki missing -> metrics-only diagnosis")
        elif not non_interactive:
            params.loki_url = _prompt(
                "Loki URL",
                default="http://127.0.0.1:3100",
            )
            if params.loki_url:
                report.decisions.append(f"Loki URL from prompt -> {params.loki_url}")
            else:
                missing.append(
                    "Loki URL (--loki-url / AGENT_LOKI_URL / discovery / prompt)"
                )
        else:
            missing.append(
                "Loki URL (--loki-url / AGENT_LOKI_URL / discovery)"
            )

    if not params.alertmanager_url:
        if allow_degraded:
            params.webhook_disabled = True
            report.decisions.append(
                "Alertmanager missing -> webhook routing disabled"
            )
        elif not non_interactive:
            params.alertmanager_url = _prompt(
                "Alertmanager URL",
                default="http://127.0.0.1:9093",
            )
            if params.alertmanager_url:
                report.decisions.append(
                    f"Alertmanager URL from prompt -> {params.alertmanager_url}"
                )
            else:
                missing.append(
                    "Alertmanager URL (--alertmanager-url / discovery / prompt)"
                )
        else:
            missing.append(
                "Alertmanager URL (--alertmanager-url / discovery)"
            )

    if params.alertmanager_url and not params.webhook_url:
        if not non_interactive:
            params.webhook_url = _prompt(
                "Alertmanager -> agent webhook URL",
                default=matrix.alertmanager_to_agent_webhook
                or "http://diagnostic-agent:8000/webhook",
            )
        if not params.webhook_url:
            missing.append("Alertmanager -> agent webhook URL (--webhook-url)")

    # Grafana URL: optional; prompt only when interactive and not set so the
    # operator can supply it or leave blank to disable annotations.
    if not params.grafana_url and not non_interactive and not allow_degraded:
        params.grafana_url = _prompt(
            "Grafana URL (Enter to skip annotations)",
            default="",
        )
        if params.grafana_url:
            report.decisions.append(
                f"Grafana URL from prompt -> {params.grafana_url}"
            )

    # Grafana annotations: optional (not required to run the agent).
    if not params.grafana_url:
        params.annotations_disabled = True
        params.grafana_annotations_enabled = False
        report.decisions.append("Grafana missing -> annotations disabled")

    # Hard gate: Prometheus URL must exist by this point.
    if not params.prometheus_url:
        raise ValueError(
            "Prometheus URL is required. Re-run after Prometheus is reachable, "
            "pass --prometheus-url, or enter it when prompted."
        )

    if missing:
        raise ValueError(
            "Incomplete install parameters (fail closed). Provide the missing "
            "values or re-run with --allow-degraded:\n"
            + "\n".join(f"  - {item}" for item in missing)
        )

    # --- LLM auto-select ---
    _resolve_llm(
        params,
        report,
        overrides,
        non_interactive=non_interactive,
        allow_degraded=allow_degraded,
    )

    # --- SMTP ---
    _resolve_smtp(params, report, overrides, non_interactive=non_interactive)

    # --- Grafana token (prompt only; provisioning is --apply) ---
    params.grafana_token = _first(
        overrides.get("grafana_token"),
        os.environ.get("AGENT_GRAFANA_TOKEN"),
        "",
    )
    if params.grafana_url and not params.grafana_token and not non_interactive:
        params.grafana_token = _prompt_secret(
            "Grafana service-account token (Enter to skip / provision later)",
            default="",
        )
    if not params.grafana_token:
        params.grafana_annotations_enabled = False
        if params.grafana_url:
            report.decisions.append(
                "No Grafana token -- annotations disabled until provisioned"
            )

    if allow_degraded:
        report.decisions.append("allow_degraded=true")

    report.decisions.append(f"preset={params.preset}")
    report.decisions.append(f"chat={params.chat_provider}/{params.chat_model}")
    report.decisions.append(
        f"placement={matrix.agent_placement} webhook={params.webhook_url}"
    )
    return params


def _resolve_preset(
    preset: str, report: DiscoveryReport, overrides: dict[str, Any]
) -> str:
    if overrides.get("preset") and overrides["preset"] != "auto":
        return str(overrides["preset"])
    if preset and preset != "auto":
        return preset
    # Heuristic: spring-micrometer if we see typical Spring-ish container names.
    spring_hints = ("platform-service", "api-gateway", "spring", "micrometer")
    for tool in report.tools:
        blob = " ".join(tool.notes + [tool.container_name]).lower()
        if any(h in blob for h in spring_hints):
            report.decisions.append("auto-preset -> spring-micrometer (container hints)")
            return "spring-micrometer"
    report.decisions.append("auto-preset -> generic-prometheus")
    return "generic-prometheus"


def _resolve_llm(
    params: InstallParams,
    report: DiscoveryReport,
    overrides: dict[str, Any],
    *,
    non_interactive: bool,
    allow_degraded: bool = False,
) -> None:
    if overrides.get("chat_provider"):
        params.chat_provider = str(overrides["chat_provider"])
        params.chat_model = str(
            overrides.get("chat_model") or params.chat_model
        )
        params.embed_provider = str(
            overrides.get("embed_provider") or params.chat_provider
        )
        params.embed_model = str(
            overrides.get("embed_model") or params.embed_model
        )
        if overrides.get("chat_model_kwargs"):
            params.chat_model_kwargs = str(overrides["chat_model_kwargs"])
        if overrides.get("embed_model_kwargs"):
            params.embed_model_kwargs = str(overrides["embed_model_kwargs"])
        report.decisions.append(f"LLM override -> {params.chat_provider}")
        return

    ollama = report.tool(ToolKind.OLLAMA)
    has_aws = bool(
        os.environ.get("AWS_ACCESS_KEY_ID")
        or os.environ.get("DIAGNOSTIC_AGENT_AWS_ACCESS_KEY_ID")
        or overrides.get("aws_access_key_id")
    )
    openai_key = _first(
        overrides.get("openai_api_key"), os.environ.get("OPENAI_API_KEY"), ""
    )
    anthropic_key = _first(
        overrides.get("anthropic_api_key"), os.environ.get("ANTHROPIC_API_KEY"), ""
    )
    google_key = _first(
        overrides.get("google_api_key"), os.environ.get("GOOGLE_API_KEY"), ""
    )

    if ollama and ollama.reachable:
        params.chat_provider = "ollama"
        params.embed_provider = "ollama"
        base = ollama.url or "http://ollama:11434"
        params.chat_model_kwargs = f'{{"base_url":"{base}"}}'
        params.embed_model_kwargs = f'{{"base_url":"{base}"}}'
        report.decisions.append(f"LLM auto -> ollama at {base}")
    elif has_aws:
        params.chat_provider = "bedrock_converse"
        params.chat_model = "amazon.nova-micro-v1:0"
        params.embed_provider = "bedrock"
        params.embed_model = "amazon.titan-embed-text-v2:0"
        region = _first(
            overrides.get("aws_region"),
            os.environ.get("AWS_REGION"),
            "us-east-1",
        )
        params.aws_region = region
        params.chat_model_kwargs = f'{{"region_name":"{region}"}}'
        params.embed_model_kwargs = f'{{"region_name":"{region}"}}'
        params.aws_access_key_id = _first(
            overrides.get("aws_access_key_id"),
            os.environ.get("DIAGNOSTIC_AGENT_AWS_ACCESS_KEY_ID"),
            os.environ.get("AWS_ACCESS_KEY_ID"),
            "",
        )
        params.aws_secret_access_key = _first(
            overrides.get("aws_secret_access_key"),
            os.environ.get("DIAGNOSTIC_AGENT_AWS_SECRET_ACCESS_KEY"),
            os.environ.get("AWS_SECRET_ACCESS_KEY"),
            "",
        )
        report.decisions.append(f"LLM auto -> bedrock ({region})")
    elif openai_key:
        params.chat_provider = "openai"
        params.chat_model = "gpt-4o-mini"
        params.embed_provider = "openai"
        params.embed_model = "text-embedding-3-small"
        params.openai_api_key = openai_key
        report.decisions.append("LLM auto -> openai")
    elif anthropic_key:
        params.chat_provider = "anthropic"
        params.chat_model = "claude-3-5-haiku-latest"
        params.embed_provider = "openai"  # embeddings still need a provider
        params.anthropic_api_key = anthropic_key
        report.decisions.append("LLM auto -> anthropic (embeddings still need config)")
    elif google_key:
        params.chat_provider = "google_genai"
        params.chat_model = "gemini-1.5-flash"
        params.embed_provider = "google_genai"
        params.embed_model = "text-embedding-004"
        params.google_api_key = google_key
        report.decisions.append("LLM auto -> google_genai")
    elif non_interactive:
        if allow_degraded:
            report.warnings.append(
                "No LLM credentials detected -- defaulting to ollama "
                "(ensure Ollama is running before starting the agent)"
            )
            return
        raise ValueError(
            "No LLM credentials detected (fail closed). Pass --chat-provider, "
            "set OPENAI_API_KEY / ANTHROPIC_API_KEY / GOOGLE_API_KEY / AWS_*, "
            "ensure Ollama is reachable, or re-run with --allow-degraded."
        )
    else:
        choice = _prompt(
            "LLM provider [ollama/openai/bedrock/anthropic/google]",
            default="ollama",
        ).lower()
        params.chat_provider = choice
        if choice == "openai":
            params.openai_api_key = _prompt_secret("OPENAI_API_KEY")
            params.chat_model = "gpt-4o-mini"
            params.embed_provider = "openai"
            params.embed_model = "text-embedding-3-small"
        elif choice == "bedrock":
            params.chat_provider = "bedrock_converse"
            params.aws_region = _prompt("AWS region", default="us-east-1")
            params.chat_model = "amazon.nova-micro-v1:0"
            params.embed_provider = "bedrock"
            params.embed_model = "amazon.titan-embed-text-v2:0"
            params.chat_model_kwargs = f'{{"region_name":"{params.aws_region}"}}'
            params.embed_model_kwargs = params.chat_model_kwargs
        elif choice == "anthropic":
            params.anthropic_api_key = _prompt_secret("ANTHROPIC_API_KEY")
            params.chat_model = "claude-3-5-haiku-latest"
        elif choice == "google":
            params.chat_provider = "google_genai"
            params.google_api_key = _prompt_secret("GOOGLE_API_KEY")
            params.chat_model = "gemini-1.5-flash"
            params.embed_provider = "google_genai"
            params.embed_model = "text-embedding-004"
        else:
            base = _prompt("Ollama base URL", default="http://127.0.0.1:11434")
            params.chat_model_kwargs = f'{{"base_url":"{base}"}}'
            params.embed_model_kwargs = f'{{"base_url":"{base}"}}'


def _resolve_smtp(
    params: InstallParams,
    report: DiscoveryReport,
    overrides: dict[str, Any],
    *,
    non_interactive: bool,
) -> None:
    mailpit = report.tool(ToolKind.MAILPIT)
    if overrides.get("email_enabled") is False:
        params.email_enabled = False
        return
    if mailpit and mailpit.reachable:
        params.email_enabled = True
        params.smtp_host = (
            mailpit.container_name
            if mailpit.container_name
            else "127.0.0.1"
        )
        params.smtp_port = 1025
        params.smtp_from = "diagnostic-agent@localhost"
        report.decisions.append(f"SMTP auto -> Mailpit ({params.smtp_host}:1025)")
        return
    if overrides.get("smtp_host"):
        params.email_enabled = True
        params.smtp_host = str(overrides["smtp_host"])
        params.smtp_port = int(overrides.get("smtp_port") or 587)
        params.smtp_from = str(overrides.get("smtp_from") or params.smtp_from)
        params.email_to = str(overrides.get("email_to") or params.email_to)
        return
    if non_interactive:
        params.email_enabled = False
        report.decisions.append("SMTP disabled (non-interactive, no Mailpit)")
        return
    enable = _prompt("Enable diagnostic email delivery? [y/N]", default="n")
    if enable.lower() not in ("y", "yes"):
        params.email_enabled = False
        return
    params.email_enabled = True
    params.smtp_host = _prompt("SMTP host", default="localhost")
    params.smtp_port = int(_prompt("SMTP port", default="587"))
    params.smtp_from = _prompt("From address", default=params.smtp_from)
    params.email_to = _prompt("To address", default=params.email_to)
    params.smtp_username = _prompt("SMTP username (empty ok)", default="")
    if params.smtp_username:
        params.smtp_password = _prompt_secret("SMTP password")
    params.smtp_starttls = (
        _prompt("STARTTLS? [Y/n]", default="y").lower() not in ("n", "no")
    )


def _first(*values: Any) -> Any:
    for v in values:
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return v
    return ""


def _prompt(message: str, *, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{message}{suffix}: ").strip()
    except EOFError:
        return default
    return value or default


def _prompt_secret(message: str, *, default: str = "") -> str:
    try:
        value = getpass.getpass(f"{message}: ").strip()
    except EOFError:
        return default
    return value or default
