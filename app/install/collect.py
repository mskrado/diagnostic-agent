"""Resolve every parameter required for a self-sufficient install."""
from __future__ import annotations

import json
import os
from typing import Any

from .models import (
    HEALTH_PATHS,
    MAILPIT_SMTP_PORT,
    DiscoveryReport,
    InstallParams,
    ReachabilityMatrix,
    ToolEndpoint,
    ToolKind,
)
from .prompt import Prompter, container_rewrite

_SECTION_TOTAL = 6

_PRESETS = ["generic-prometheus", "spring-micrometer"]
_PROVIDERS = ["ollama", "openai", "bedrock", "anthropic", "google"]

# Interactive provider choice -> stored provider id.
_PROVIDER_IDS = {
    "ollama": "ollama",
    "openai": "openai",
    "bedrock": "bedrock_converse",
    "anthropic": "anthropic",
    "google": "google_genai",
}


def collect(
    report: DiscoveryReport,
    *,
    preset: str = "auto",
    non_interactive: bool = False,
    allow_degraded: bool = False,
    accept_defaults: bool = False,
    assume_yes: bool = False,
    probe_timeout: float = 3.0,
    overrides: dict[str, Any] | None = None,
) -> InstallParams:
    """Merge discovery + env/flags + prompts into :class:`InstallParams`.

    Default interactive behaviour **confirms every parameter** after discovery
    (discovered / flag / env values are offered as defaults; Enter accepts), then
    shows a review summary before the bundle is written.

    Default is also **fail closed**: Prometheus, Loki, Alertmanager (+ webhook),
    and a usable LLM must be resolved before a complete install bundle is
    emitted. Soft-degrade requires ``allow_degraded=True`` (``--allow-degraded``).
    Grafana annotations and SMTP remain optional delivery channels.
    """
    overrides = overrides or {}
    interactive = not non_interactive
    prompter = Prompter(interactive=interactive, accept_defaults=accept_defaults)

    while True:
        params = InstallParams()
        missing: list[str] = []
        _seed_params(params, report, overrides)
        params.preset = _resolve_preset(preset, report, overrides)

        if interactive:
            _confirm_core_parameters(
                params,
                report,
                prompter,
                missing,
                allow_degraded=allow_degraded,
                probe_timeout=probe_timeout,
            )
        else:
            _apply_non_interactive_gates(
                params, report, missing, allow_degraded=allow_degraded
            )

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

        _resolve_llm(
            params,
            report,
            overrides,
            prompter,
            non_interactive=non_interactive,
            allow_degraded=allow_degraded,
        )
        _resolve_smtp(
            params, report, overrides, prompter, non_interactive=non_interactive
        )
        _resolve_grafana_token(params, report, overrides, prompter)

        if not interactive or accept_defaults or assume_yes:
            break
        prompter.summary("Review", _summary_rows(params))
        if prompter.yes_no("Write the install bundle with these settings?", default=True):
            break
        report.decisions.append("operator re-entered parameters at review")
        print("\nRestarting parameter collection...")

    if allow_degraded:
        report.decisions.append("allow_degraded=true")
    report.decisions.append(f"preset={params.preset}")
    report.decisions.append(f"chat={params.chat_provider}/{params.chat_model}")
    report.decisions.append(
        f"placement={report.reachability.agent_placement} webhook={params.webhook_url}"
    )
    return params


def _seed_params(
    params: InstallParams, report: DiscoveryReport, overrides: dict[str, Any]
) -> None:
    """Seed candidates from flag -> env -> discovery."""
    matrix = report.reachability
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


def _summary_rows(params: InstallParams) -> list[tuple[str, str]]:
    rows = [
        ("preset", params.preset),
        ("prometheus", params.prometheus_url),
        ("loki", params.loki_url or "(metrics-only)"),
        ("alertmanager", params.alertmanager_url or "(webhook disabled)"),
        ("webhook", "(disabled)" if params.webhook_disabled else params.webhook_url),
        ("grafana", params.grafana_url or "(annotations disabled)"),
        ("grafana token", "***" if params.grafana_token else "(none)"),
        ("chat", f"{params.chat_provider}/{params.chat_model}"),
        ("embeddings", f"{params.embed_provider}/{params.embed_model}"),
    ]
    if params.email_enabled:
        rows.append(("email", f"{params.smtp_host}:{params.smtp_port} -> {params.email_to}"))
    else:
        rows.append(("email", "(disabled)"))
    return rows


def _confirm_endpoint(
    prompter: Prompter,
    report: DiscoveryReport,
    label: str,
    current: str,
    kind: ToolKind | None,
    *,
    fallback: str,
    allow_empty: bool,
    probe_timeout: float,
    help_text: str = "",
) -> str:
    """Confirm one endpoint URL, then check reachability and container routing."""
    default = current or ("" if allow_empty else fallback)
    url = prompter.url(
        label, default=default, allow_empty=allow_empty, help_text=help_text
    )
    if not url:
        return ""

    # Discovery already proved the seeded candidate; only probe operator edits.
    changed = url != default
    if changed and kind is not None and not _probe_ok(url, kind, probe_timeout):
        prompter.warn(f"{url} did not answer a health check from this host")
        report.warnings.append(f"{label}: {url} unreachable during install")

    rewrite = container_rewrite(url)
    if rewrite and prompter.yes_no(
        f"The agent runs in a container; use {rewrite} instead?",
        default=True,
        help_text="Loopback addresses point at the agent container, not your host.",
    ):
        report.decisions.append(f"{label}: rewritten for container -> {rewrite}")
        return rewrite
    return url


def _probe_ok(url: str, kind: ToolKind, timeout: float) -> bool:
    paths = HEALTH_PATHS.get(kind)
    if not paths:
        return True
    from .discover import _http_probe

    try:
        ok, _, _ = _http_probe(url, paths, timeout=timeout)
    except Exception:  # noqa: BLE001 - a failed probe is advisory only
        return False
    return ok


def _confirm_core_parameters(
    params: InstallParams,
    report: DiscoveryReport,
    prompter: Prompter,
    missing: list[str],
    *,
    allow_degraded: bool,
    probe_timeout: float,
) -> None:
    """Interactive: confirm every core parameter (Enter keeps the default)."""
    report.decisions.append("interactive confirm: every parameter")
    matrix = report.reachability

    prompter.section("Workspace preset", total=_SECTION_TOTAL)
    params.preset = prompter.choice(
        "Metrics/logs preset",
        _PRESETS,
        default=params.preset or _PRESETS[0],
        help_text="Selects PromQL templates and log label conventions.",
    )

    prompter.section("Observability endpoints (agent reads these)", total=_SECTION_TOTAL)
    params.prometheus_url = _confirm_endpoint(
        prompter,
        report,
        "Prometheus URL",
        params.prometheus_url,
        ToolKind.PROMETHEUS,
        fallback="http://127.0.0.1:9090",
        allow_empty=False,
        probe_timeout=probe_timeout,
        help_text="Required: metrics are the primary diagnosis signal.",
    )
    report.decisions.append(f"Prometheus URL confirmed -> {params.prometheus_url}")

    params.loki_url = _confirm_endpoint(
        prompter,
        report,
        "Loki URL",
        params.loki_url,
        ToolKind.LOKI,
        fallback="http://127.0.0.1:3100",
        allow_empty=allow_degraded,
        probe_timeout=probe_timeout,
        help_text=(
            "Enter to skip (metrics-only)."
            if allow_degraded
            else "Required: log evidence for runbook correlation."
        ),
    )
    if params.loki_url:
        report.decisions.append(f"Loki URL confirmed -> {params.loki_url}")
    else:
        params.metrics_only = True
        report.decisions.append("Loki missing -> metrics-only diagnosis")

    prompter.section("Alert routing (Alertmanager -> agent)", total=_SECTION_TOTAL)
    params.alertmanager_url = _confirm_endpoint(
        prompter,
        report,
        "Alertmanager URL",
        params.alertmanager_url,
        ToolKind.ALERTMANAGER,
        fallback="http://127.0.0.1:9093",
        allow_empty=allow_degraded,
        probe_timeout=probe_timeout,
        help_text=(
            "Enter to skip webhook wiring."
            if allow_degraded
            else "Required: Alertmanager triggers every diagnosis."
        ),
    )
    if params.alertmanager_url:
        report.decisions.append(
            f"Alertmanager URL confirmed -> {params.alertmanager_url}"
        )
        params.webhook_url = prompter.url(
            "Alertmanager -> agent webhook URL",
            default=(
                params.webhook_url
                or matrix.alertmanager_to_agent_webhook
                or "http://diagnostic-agent:8000/webhook"
            ),
            allow_empty=False,
            help_text="Must be routable from Alertmanager, not from your shell.",
        )
        report.decisions.append(f"Webhook URL confirmed -> {params.webhook_url}")
    else:
        params.webhook_disabled = True
        report.decisions.append("Alertmanager missing -> webhook routing disabled")

    prompter.section("Grafana annotations (optional)", total=_SECTION_TOTAL)
    params.grafana_url = _confirm_endpoint(
        prompter,
        report,
        "Grafana URL",
        params.grafana_url,
        ToolKind.GRAFANA,
        fallback="",
        allow_empty=True,
        probe_timeout=probe_timeout,
        help_text="Enter to skip annotation delivery.",
    )
    if params.grafana_url:
        report.decisions.append(f"Grafana URL confirmed -> {params.grafana_url}")
    else:
        params.annotations_disabled = True
        params.grafana_annotations_enabled = False
        report.decisions.append("Grafana missing -> annotations disabled")

    _ = missing  # interactive prompts are required-or-blank; nothing to accumulate


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
        params.chat_model_kwargs = json.dumps({"base_url": base})
        params.embed_model_kwargs = json.dumps({"base_url": base})
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
        params.chat_model_kwargs = json.dumps({"region_name": region})
        params.embed_model_kwargs = json.dumps({"region_name": region})
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


def _kwargs_value(raw: str, key: str, fallback: str) -> str:
    try:
        return str(json.loads(raw or "{}").get(key) or fallback)
    except (json.JSONDecodeError, TypeError, ValueError):
        return fallback


def _confirm_llm(
    params: InstallParams,
    report: DiscoveryReport,
    prompter: Prompter,
    *,
    allow_degraded: bool,
) -> None:
    """Always confirm LLM settings — including the credentials the agent needs."""
    prompter.section("Diagnosis LLM", total=_SECTION_TOTAL)
    display = {"bedrock_converse": "bedrock", "google_genai": "google"}.get(
        params.chat_provider, params.chat_provider or "ollama"
    )
    choice = prompter.choice(
        "LLM provider",
        _PROVIDERS,
        default=display if display in _PROVIDERS else "ollama",
        help_text="Runs the diagnostic graph and embeds runbooks for retrieval.",
    )
    params.chat_provider = _PROVIDER_IDS[choice]

    if choice == "openai":
        params.chat_model = prompter.text(
            "Chat model", default=params.chat_model or "gpt-4o-mini"
        )
        params.embed_provider = "openai"
        params.embed_model = prompter.text(
            "Embed model", default=params.embed_model or "text-embedding-3-small"
        )
        params.openai_api_key = _require_key(
            params.openai_api_key,
            "OPENAI_API_KEY",
            prompter,
            allow_degraded=allow_degraded,
        )
    elif choice == "bedrock":
        params.aws_region = prompter.text(
            "AWS region", default=params.aws_region or "us-east-1", allow_empty=False
        )
        params.chat_model = prompter.text(
            "Chat model", default=params.chat_model or "amazon.nova-micro-v1:0"
        )
        params.embed_provider = "bedrock"
        params.embed_model = prompter.text(
            "Embed model",
            default=params.embed_model or "amazon.titan-embed-text-v2:0",
        )
        params.chat_model_kwargs = json.dumps({"region_name": params.aws_region})
        params.embed_model_kwargs = params.chat_model_kwargs
        _confirm_aws_credentials(params, report, prompter)
    elif choice == "anthropic":
        params.chat_model = prompter.text(
            "Chat model", default=params.chat_model or "claude-3-5-haiku-latest"
        )
        params.anthropic_api_key = _require_key(
            params.anthropic_api_key,
            "ANTHROPIC_API_KEY",
            prompter,
            allow_degraded=allow_degraded,
        )
        report.warnings.append(
            "Anthropic has no embeddings API -- set AGENT_EMBED_* for RAG"
        )
    elif choice == "google":
        params.chat_model = prompter.text(
            "Chat model", default=params.chat_model or "gemini-1.5-flash"
        )
        params.embed_provider = "google_genai"
        params.embed_model = prompter.text(
            "Embed model", default=params.embed_model or "text-embedding-004"
        )
        params.google_api_key = _require_key(
            params.google_api_key,
            "GOOGLE_API_KEY",
            prompter,
            allow_degraded=allow_degraded,
        )
    else:
        params.embed_provider = "ollama"
        params.chat_model = prompter.text(
            "Chat model", default=params.chat_model or "mistral:7b-instruct"
        )
        params.embed_model = prompter.text(
            "Embed model", default=params.embed_model or "nomic-embed-text"
        )
        base = prompter.url(
            "Ollama base URL",
            default=_kwargs_value(
                params.chat_model_kwargs, "base_url", "http://127.0.0.1:11434"
            ),
            allow_empty=False,
        )
        rewrite = container_rewrite(base)
        if rewrite and prompter.yes_no(
            f"The agent runs in a container; use {rewrite} instead?", default=True
        ):
            base = rewrite
            report.decisions.append(f"Ollama base rewritten for container -> {base}")
        params.chat_model_kwargs = json.dumps({"base_url": base})
        params.embed_model_kwargs = json.dumps({"base_url": base})

    report.decisions.append(
        f"LLM confirmed -> {params.chat_provider}/{params.chat_model}"
    )


def _require_key(
    current: str,
    env_name: str,
    prompter: Prompter,
    *,
    allow_degraded: bool,
) -> str:
    """Prompt for a provider credential the agent cannot run without."""
    if current:
        return current
    value = prompter.secret(
        env_name,
        allow_empty=allow_degraded,
        help_text="Written to agent/.env only; never to install-report.json.",
    )
    if value or allow_degraded:
        return value
    raise ValueError(
        f"{env_name} is required for the selected provider (fail closed). "
        f"Set {env_name} in the environment, or re-run with --allow-degraded."
    )


def _confirm_aws_credentials(
    params: InstallParams, report: DiscoveryReport, prompter: Prompter
) -> None:
    """Bedrock needs either explicit keys in .env or ambient AWS credentials."""
    if params.aws_access_key_id and params.aws_secret_access_key:
        return
    if prompter.yes_no(
        "Use ambient AWS credentials (instance role / shared config)?",
        default=True,
        help_text="Answer n to write explicit keys into agent/.env.",
    ):
        report.decisions.append("Bedrock -> ambient AWS credentials")
        report.warnings.append(
            "No AWS keys in agent/.env -- the container must inherit credentials "
            "(instance role, mounted ~/.aws, or compose environment)"
        )
        return
    params.aws_access_key_id = prompter.secret("AWS_ACCESS_KEY_ID", allow_empty=False)
    params.aws_secret_access_key = prompter.secret(
        "AWS_SECRET_ACCESS_KEY", allow_empty=False
    )
    report.decisions.append("Bedrock -> explicit AWS keys in agent/.env")


def _resolve_llm(
    params: InstallParams,
    report: DiscoveryReport,
    overrides: dict[str, Any],
    prompter: Prompter,
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

    _confirm_llm(params, report, prompter, allow_degraded=allow_degraded)


def _mailpit_smtp_host(mailpit: ToolEndpoint) -> str:
    """Pick an SMTP host the agent container can reach for Mailpit."""
    if mailpit.container_name:
        return mailpit.container_name
    return "host.docker.internal"


def _seed_mailpit_smtp(params: InstallParams, report: DiscoveryReport) -> bool:
    """Seed Mailpit client SMTP settings when Mailpit is present.

    Mailpit is usable when HTTP-reachable *or* when Docker found the container
    (SMTP is on :1025 even if the UI probe failed).
    """
    mailpit = report.tool(ToolKind.MAILPIT)
    if not mailpit or not (mailpit.reachable or mailpit.container_name):
        return False
    params.email_enabled = True
    params.smtp_host = _mailpit_smtp_host(mailpit)
    params.smtp_port = MAILPIT_SMTP_PORT
    params.smtp_from = params.smtp_from or "diagnostic-agent@localhost"
    params.smtp_username = ""
    params.smtp_password = ""
    params.smtp_starttls = False
    report.decisions.append(
        f"SMTP seed -> Mailpit ({params.smtp_host}:{MAILPIT_SMTP_PORT})"
    )
    return True


def _apply_mailpit_smtp_defaults(params: InstallParams) -> None:
    """Interactive fallback: Mailpit-style client settings (no auth / no TLS)."""
    if not params.smtp_host:
        params.smtp_host = "host.docker.internal"
    if not params.smtp_port:
        params.smtp_port = MAILPIT_SMTP_PORT


def _resolve_smtp(
    params: InstallParams,
    report: DiscoveryReport,
    overrides: dict[str, Any],
    prompter: Prompter,
    *,
    non_interactive: bool,
) -> None:
    if overrides.get("email_enabled") is False:
        params.email_enabled = False
        return

    if _seed_mailpit_smtp(params, report):
        pass
    elif overrides.get("smtp_host"):
        params.email_enabled = True
        params.smtp_host = str(overrides["smtp_host"])
        params.smtp_port = int(overrides.get("smtp_port") or MAILPIT_SMTP_PORT)
        params.smtp_from = str(overrides.get("smtp_from") or params.smtp_from)
        params.email_to = str(overrides.get("email_to") or params.email_to)
        report.decisions.append(f"SMTP seed -> {params.smtp_host}:{params.smtp_port}")
    elif non_interactive:
        params.email_enabled = False
        report.decisions.append("SMTP disabled (non-interactive, no Mailpit)")
        return
    else:
        _apply_mailpit_smtp_defaults(params)

    if non_interactive:
        return

    prompter.section("Diagnostic email (optional)", total=_SECTION_TOTAL)
    if not prompter.yes_no(
        "Enable diagnostic email delivery?",
        default=params.email_enabled,
        help_text=(
            "The agent's hypothesis report, separate from Alertmanager mail. "
            "Defaults target Mailpit (container or host.docker.internal :1025)."
        ),
    ):
        params.email_enabled = False
        report.decisions.append("SMTP confirmed disabled")
        return

    params.email_enabled = True
    _apply_mailpit_smtp_defaults(params)
    params.smtp_host = prompter.text(
        "SMTP host",
        default=params.smtp_host,
        allow_empty=False,
    )
    if params.smtp_host in ("localhost", "127.0.0.1", "::1"):
        prompter.warn(
            f"{params.smtp_host} resolves inside the agent container -- use a "
            "container name or host.docker.internal for a host relay"
        )
        report.warnings.append(
            f"SMTP host {params.smtp_host} may be unreachable from the container"
        )
    params.smtp_port = prompter.port(
        "SMTP port", default=params.smtp_port or MAILPIT_SMTP_PORT
    )
    params.smtp_from = prompter.text("From address", default=params.smtp_from)
    params.email_to = prompter.text("To address", default=params.email_to)
    params.smtp_username = prompter.text(
        "SMTP username (empty ok)", default=params.smtp_username or ""
    )
    if params.smtp_username and not params.smtp_password:
        params.smtp_password = prompter.secret("SMTP password")
    params.smtp_starttls = prompter.yes_no(
        "Use STARTTLS?", default=params.smtp_starttls
    )
    report.decisions.append(
        f"SMTP confirmed -> {params.smtp_host}:{params.smtp_port}"
    )


def _resolve_grafana_token(
    params: InstallParams,
    report: DiscoveryReport,
    overrides: dict[str, Any],
    prompter: Prompter,
) -> None:
    params.grafana_token = _first(
        overrides.get("grafana_token"),
        os.environ.get("AGENT_GRAFANA_TOKEN"),
        "",
    )
    if params.grafana_url and not params.grafana_token:
        params.grafana_token = prompter.secret(
            "Grafana service-account token (Enter to skip / provision later)"
        )
    if not params.grafana_token:
        params.grafana_annotations_enabled = False
        if params.grafana_url:
            report.decisions.append(
                "No Grafana token -- annotations disabled until provisioned"
            )
    else:
        params.grafana_annotations_enabled = True


def _first(*values: Any) -> Any:
    for v in values:
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return v
    return ""
