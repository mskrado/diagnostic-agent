"""Resolve every parameter required for a self-sufficient install."""
from __future__ import annotations

import getpass
import os
from typing import Any

from .models import DiscoveryReport, InstallParams, ReachabilityMatrix, ToolKind


def collect(
    report: DiscoveryReport,
    *,
    preset: str = "auto",
    non_interactive: bool = False,
    allow_degraded: bool = False,
    overrides: dict[str, Any] | None = None,
) -> InstallParams:
    """Merge discovery + env/flags + prompts into :class:`InstallParams`.

    Default interactive behaviour **confirms every parameter** after discovery
    (discovered / flag / env values are offered as defaults; Enter accepts).

    Default is also **fail closed**: Prometheus, Loki, Alertmanager (+ webhook),
    and a usable LLM must be resolved before a complete install bundle is
    emitted. Soft-degrade requires ``allow_degraded=True`` (``--allow-degraded``).
    Grafana annotations and SMTP remain optional delivery channels.
    """
    overrides = overrides or {}
    params = InstallParams()
    matrix = report.reachability
    missing: list[str] = []

    # --- Seed from flag → env → discovery (candidates for confirmation) ---
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

    if not non_interactive:
        _confirm_all_parameters(
            params,
            report,
            matrix,
            missing,
            allow_degraded=allow_degraded,
        )
    else:
        _apply_non_interactive_gates(
            params, report, missing, allow_degraded=allow_degraded
        )

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

    # --- LLM (seed + confirm interactively; fail closed when non-interactive) ---
    _resolve_llm(
        params,
        report,
        overrides,
        non_interactive=non_interactive,
        allow_degraded=allow_degraded,
    )

    # --- SMTP ---
    _resolve_smtp(params, report, overrides, non_interactive=non_interactive)

    # --- Grafana token ---
    params.grafana_token = _first(
        overrides.get("grafana_token"),
        os.environ.get("AGENT_GRAFANA_TOKEN"),
        "",
    )
    if params.grafana_url and not non_interactive:
        entered = _prompt_secret(
            "Grafana service-account token (Enter to keep existing / skip)",
            default="",
        )
        if entered:
            params.grafana_token = entered
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


def _confirm_all_parameters(
    params: InstallParams,
    report: DiscoveryReport,
    matrix: ReachabilityMatrix,
    missing: list[str],
    *,
    allow_degraded: bool,
) -> None:
    """Interactive: confirm every install parameter (Enter keeps the default)."""
    report.decisions.append("interactive confirm: every parameter")

    params.preset = _prompt(
        "Metrics/logs preset [generic-prometheus/spring-micrometer]",
        default=params.preset or "generic-prometheus",
    )
    if params.preset not in ("generic-prometheus", "spring-micrometer"):
        report.warnings.append(
            f"Unusual preset {params.preset!r}; continuing with operator value"
        )

    params.prometheus_url = _prompt(
        "Prometheus URL",
        default=params.prometheus_url or "http://127.0.0.1:9090",
    )
    report.decisions.append(f"Prometheus URL confirmed -> {params.prometheus_url}")

    loki_default = params.loki_url or (
        "" if allow_degraded else "http://127.0.0.1:3100"
    )
    loki_label = (
        "Loki URL (Enter for metrics-only)"
        if allow_degraded
        else "Loki URL"
    )
    params.loki_url = _prompt(loki_label, default=loki_default)
    if params.loki_url:
        report.decisions.append(f"Loki URL confirmed -> {params.loki_url}")
    elif allow_degraded:
        params.metrics_only = True
        report.decisions.append("Loki missing -> metrics-only diagnosis")
    else:
        missing.append("Loki URL (--loki-url / AGENT_LOKI_URL / discovery / prompt)")

    am_default = params.alertmanager_url or (
        "" if allow_degraded else "http://127.0.0.1:9093"
    )
    am_label = (
        "Alertmanager URL (Enter to skip webhook wiring)"
        if allow_degraded
        else "Alertmanager URL"
    )
    params.alertmanager_url = _prompt(am_label, default=am_default)
    if params.alertmanager_url:
        report.decisions.append(
            f"Alertmanager URL confirmed -> {params.alertmanager_url}"
        )
        webhook_default = (
            params.webhook_url
            or matrix.alertmanager_to_agent_webhook
            or "http://diagnostic-agent:8000/webhook"
        )
        params.webhook_url = _prompt(
            "Alertmanager -> agent webhook URL",
            default=webhook_default,
        )
        if params.webhook_url:
            report.decisions.append(f"Webhook URL confirmed -> {params.webhook_url}")
        else:
            missing.append("Alertmanager -> agent webhook URL (--webhook-url)")
    elif allow_degraded:
        params.webhook_disabled = True
        report.decisions.append("Alertmanager missing -> webhook routing disabled")
    else:
        missing.append(
            "Alertmanager URL (--alertmanager-url / discovery / prompt)"
        )

    params.grafana_url = _prompt(
        "Grafana URL (Enter to skip annotations)",
        default=params.grafana_url or "",
    )
    if params.grafana_url:
        report.decisions.append(f"Grafana URL confirmed -> {params.grafana_url}")
    else:
        params.annotations_disabled = True
        params.grafana_annotations_enabled = False
        report.decisions.append("Grafana missing -> annotations disabled")


def _apply_non_interactive_gates(
    params: InstallParams,
    report: DiscoveryReport,
    missing: list[str],
    *,
    allow_degraded: bool,
) -> None:
    """Non-interactive: fail closed or degrade — never prompt."""
    if not params.loki_url:
        if allow_degraded:
            params.metrics_only = True
            report.decisions.append("Loki missing -> metrics-only diagnosis")
        else:
            missing.append("Loki URL (--loki-url / AGENT_LOKI_URL / discovery)")
    if not params.alertmanager_url:
        if allow_degraded:
            params.webhook_disabled = True
            report.decisions.append(
                "Alertmanager missing -> webhook routing disabled"
            )
        else:
            missing.append("Alertmanager URL (--alertmanager-url / discovery)")
    elif not params.webhook_url:
        missing.append("Alertmanager -> agent webhook URL (--webhook-url)")

    if not params.grafana_url:
        params.annotations_disabled = True
        params.grafana_annotations_enabled = False
        report.decisions.append("Grafana missing -> annotations disabled")


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


def _seed_llm_from_environment(
    params: InstallParams,
    report: DiscoveryReport,
    overrides: dict[str, Any],
) -> bool:
    """Populate LLM fields from overrides / discovery / env. Return True if seeded."""
    if overrides.get("chat_provider"):
        params.chat_provider = str(overrides["chat_provider"])
        params.chat_model = str(overrides.get("chat_model") or params.chat_model)
        params.embed_provider = str(
            overrides.get("embed_provider") or params.chat_provider
        )
        params.embed_model = str(overrides.get("embed_model") or params.embed_model)
        if overrides.get("chat_model_kwargs"):
            params.chat_model_kwargs = str(overrides["chat_model_kwargs"])
        if overrides.get("embed_model_kwargs"):
            params.embed_model_kwargs = str(overrides["embed_model_kwargs"])
        report.decisions.append(f"LLM seed -> {params.chat_provider} (override)")
        return True

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
        report.decisions.append(f"LLM seed -> ollama at {base}")
        return True
    if has_aws:
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
        report.decisions.append(f"LLM seed -> bedrock ({region})")
        return True
    if openai_key:
        params.chat_provider = "openai"
        params.chat_model = "gpt-4o-mini"
        params.embed_provider = "openai"
        params.embed_model = "text-embedding-3-small"
        params.openai_api_key = openai_key
        report.decisions.append("LLM seed -> openai")
        return True
    if anthropic_key:
        params.chat_provider = "anthropic"
        params.chat_model = "claude-3-5-haiku-latest"
        params.embed_provider = "openai"
        params.anthropic_api_key = anthropic_key
        report.decisions.append("LLM seed -> anthropic")
        return True
    if google_key:
        params.chat_provider = "google_genai"
        params.chat_model = "gemini-1.5-flash"
        params.embed_provider = "google_genai"
        params.embed_model = "text-embedding-004"
        params.google_api_key = google_key
        report.decisions.append("LLM seed -> google_genai")
        return True
    return False


def _confirm_llm(params: InstallParams, report: DiscoveryReport) -> None:
    """Always confirm LLM settings in interactive mode."""
    default_provider = params.chat_provider or "ollama"
    # Normalize bedrock_converse display/choice aliases.
    display = {
        "bedrock_converse": "bedrock",
        "google_genai": "google",
    }.get(default_provider, default_provider)
    choice = _prompt(
        "LLM provider [ollama/openai/bedrock/anthropic/google]",
        default=display,
    ).lower()
    if choice == "openai":
        params.chat_provider = "openai"
        params.chat_model = _prompt("Chat model", default=params.chat_model or "gpt-4o-mini")
        params.embed_provider = "openai"
        params.embed_model = _prompt(
            "Embed model", default=params.embed_model or "text-embedding-3-small"
        )
        if not params.openai_api_key:
            params.openai_api_key = _prompt_secret("OPENAI_API_KEY")
    elif choice == "bedrock":
        params.chat_provider = "bedrock_converse"
        params.aws_region = _prompt(
            "AWS region", default=params.aws_region or "us-east-1"
        )
        params.chat_model = _prompt(
            "Chat model", default=params.chat_model or "amazon.nova-micro-v1:0"
        )
        params.embed_provider = "bedrock"
        params.embed_model = _prompt(
            "Embed model",
            default=params.embed_model or "amazon.titan-embed-text-v2:0",
        )
        params.chat_model_kwargs = f'{{"region_name":"{params.aws_region}"}}'
        params.embed_model_kwargs = params.chat_model_kwargs
    elif choice == "anthropic":
        params.chat_provider = "anthropic"
        params.chat_model = _prompt(
            "Chat model", default=params.chat_model or "claude-3-5-haiku-latest"
        )
        if not params.anthropic_api_key:
            params.anthropic_api_key = _prompt_secret("ANTHROPIC_API_KEY")
    elif choice == "google":
        params.chat_provider = "google_genai"
        params.chat_model = _prompt(
            "Chat model", default=params.chat_model or "gemini-1.5-flash"
        )
        params.embed_provider = "google_genai"
        params.embed_model = _prompt(
            "Embed model", default=params.embed_model or "text-embedding-004"
        )
        if not params.google_api_key:
            params.google_api_key = _prompt_secret("GOOGLE_API_KEY")
    else:
        params.chat_provider = "ollama"
        params.embed_provider = "ollama"
        params.chat_model = _prompt(
            "Chat model", default=params.chat_model or "mistral:7b-instruct"
        )
        params.embed_model = _prompt(
            "Embed model", default=params.embed_model or "nomic-embed-text"
        )
        base = "http://127.0.0.1:11434"
        if params.chat_model_kwargs and "base_url" in params.chat_model_kwargs:
            # Prefer seeded base_url when present in JSON kwargs.
            try:
                import json

                base = str(json.loads(params.chat_model_kwargs).get("base_url") or base)
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        base = _prompt("Ollama base URL", default=base)
        params.chat_model_kwargs = f'{{"base_url":"{base}"}}'
        params.embed_model_kwargs = f'{{"base_url":"{base}"}}'
    report.decisions.append(
        f"LLM confirmed -> {params.chat_provider}/{params.chat_model}"
    )


def _resolve_llm(
    params: InstallParams,
    report: DiscoveryReport,
    overrides: dict[str, Any],
    *,
    non_interactive: bool,
    allow_degraded: bool = False,
) -> None:
    seeded = _seed_llm_from_environment(params, report, overrides)

    if non_interactive:
        if seeded:
            return
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

    _confirm_llm(params, report)


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
            mailpit.container_name if mailpit.container_name else "127.0.0.1"
        )
        params.smtp_port = 1025
        params.smtp_from = "diagnostic-agent@localhost"
        report.decisions.append(f"SMTP seed -> Mailpit ({params.smtp_host}:1025)")
    elif overrides.get("smtp_host"):
        params.email_enabled = True
        params.smtp_host = str(overrides["smtp_host"])
        params.smtp_port = int(overrides.get("smtp_port") or 587)
        params.smtp_from = str(overrides.get("smtp_from") or params.smtp_from)
        params.email_to = str(overrides.get("email_to") or params.email_to)
        report.decisions.append(f"SMTP seed -> {params.smtp_host}:{params.smtp_port}")
    elif non_interactive:
        params.email_enabled = False
        report.decisions.append("SMTP disabled (non-interactive, no Mailpit)")
        return

    if non_interactive:
        # Seeded Mailpit / override already applied; nothing to prompt.
        return

    default_enable = "y" if params.email_enabled else "n"
    enable = _prompt(
        "Enable diagnostic email delivery? [y/N]",
        default=default_enable,
    )
    if enable.lower() not in ("y", "yes"):
        params.email_enabled = False
        report.decisions.append("SMTP confirmed disabled")
        return
    params.email_enabled = True
    params.smtp_host = _prompt("SMTP host", default=params.smtp_host or "localhost")
    params.smtp_port = int(_prompt("SMTP port", default=str(params.smtp_port or 587)))
    params.smtp_from = _prompt("From address", default=params.smtp_from)
    params.email_to = _prompt("To address", default=params.email_to)
    params.smtp_username = _prompt(
        "SMTP username (empty ok)", default=params.smtp_username or ""
    )
    if params.smtp_username and not params.smtp_password:
        params.smtp_password = _prompt_secret("SMTP password")
    params.smtp_starttls = (
        _prompt(
            "STARTTLS? [Y/n]",
            default="y" if params.smtp_starttls else "n",
        ).lower()
        not in ("n", "no")
    )
    report.decisions.append(
        f"SMTP confirmed -> {params.smtp_host}:{params.smtp_port}"
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
