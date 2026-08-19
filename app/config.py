"""Runtime configuration, sourced from environment variables.

All defaults assume the agent runs inside a Docker network where service DNS
names (e.g. ``prometheus``, ``loki``, ``grafana``) resolve directly.

Integration into a host project is driven by ``AGENT_PROFILE_DIR`` (an
integration profile directory). See ``app.profile``.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENT_", env_file=".env", extra="ignore")

    # --- Integration profile ---
    # Directory with service_map.yaml, metrics_profile.yaml, logs_profile.yaml,
    # redaction.yaml, prompt_profile.yaml (and optional runbooks/).
    # Deliberately empty: no host project is the default. Set it per deployment
    # (compose/.env) — e.g. examples/spring-modular-monolith for a Spring host.
    # Empty means built-in preset only.
    profile_dir: str = ""
    # Built-in preset used when profile files omit a section / for extends chain.
    # Use "spring-micrometer" for Spring Boot hosts, "generic-prometheus" otherwise.
    default_preset: str = "generic-prometheus"
    # Refuse to start when the resolved profile yields zero redaction rules.
    # Guards against a misconfigured/empty profile silently shipping tenant data
    # into reports, audit logs, and Grafana annotations.
    require_redaction: bool = True

    # --- Data sources (internal Docker DNS names by default) ---
    prometheus_url: str = "http://prometheus:9090"
    loki_url: str = "http://loki:3100"
    grafana_url: str = "http://grafana:3000"
    # Grafana service-account token (Viewer + annotations:write). Empty disables
    # Grafana calls (alerts pull + annotation delivery) gracefully.
    grafana_token: str = ""

    # --- LLM / embeddings (LangChain init_chat_model / init_embeddings) ---
    # Provider names match LangChain model_provider / provider strings.
    # Credentials come from standard SDK env vars (OPENAI_API_KEY, AWS_REGION, …).
    chat_provider: str = "ollama"
    chat_model: str = "mistral:7b-instruct"
    embed_provider: str = "ollama"
    embed_model: str = "nomic-embed-text"
    llm_temperature: float = 0.1
    # Max completion tokens for chat/structured diagnosis. Nova Converse ToolUse
    # fails with ModelErrorException when the Diagnosis JSON is truncated.
    chat_max_tokens: int = 8192
    # JSON passthrough for provider-specific kwargs (base_url, region_name, …).
    chat_model_kwargs: str = "{}"
    embed_model_kwargs: str = "{}"

    # --- RAG ---
    rag_enabled: bool = True
    # Empty → resolve from profile (profile/runbooks) then package-root runbooks/.
    runbooks_path: str = ""
    chroma_path: str = str(_BASE_DIR / "chroma_db")
    # Per-family retrieval + overall cap for mixed-error log samples.
    rag_top_k: int = 2
    rag_max_chunks: int = 8

    # --- Retrieval tuning ---
    loki_lookback_minutes: int = 15
    loki_limit: int = 500
    metrics_window: str = "5m"

    # --- Delivery ---
    audit_log_dir: str = str(_BASE_DIR / "audit")
    grafana_annotations_enabled: bool = True
    # Explicit routing is opt-in so hosts keep today's linear, read-only flow
    # until they deliberately enable the new severity gate / route recording.
    routing_enabled: bool = False

    # SMTP diagnostic email (separate from Alertmanager alert email).
    email_enabled: bool = True
    email_to: str = "dev-alerts@localhost"
    email_subject_prefix: str = "diagnostic"
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_from: str = "diagnostic-agent@localhost"
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = False
    smtp_timeout: float = 15.0
    # PagerDuty outbound escalation / note delivery. Default OFF.
    pagerduty_enabled: bool = False
    pagerduty_api_url: str = "https://api.pagerduty.com"
    pagerduty_api_token: str = ""
    pagerduty_service_id: str = ""
    pagerduty_from_email: str = ""
    pagerduty_timeout: float = 10.0
    # --- Execution (Track B; default OFF, opt-in per host) ---
    # Master switch. When false, the sandbox refuses to run (defense in depth;
    # the graph branch is also gated in #52).
    exec_enabled: bool = False
    # Path to execution_profile.yaml. Empty -> resolve from the active profile dir.
    exec_profile_path: str = ""

    # --- Dependency map ---
    # Empty → resolve from profile service_map.yaml then package-root default.
    service_map_path: str = ""

    def resolved_service_map_path(self) -> str:
        """Topology file path, or "" when no profile supplies one."""
        if self.service_map_path:
            return self.service_map_path
        from .profile import get_profile

        return get_profile().service_map_path or ""

    def resolved_runbooks_path(self) -> str:
        if self.runbooks_path:
            return self.runbooks_path
        from .profile import get_profile

        profile = get_profile()
        if profile.runbooks_path:
            return profile.runbooks_path
        return str(_BASE_DIR / "runbooks")

    def resolved_exec_profile_path(self) -> str:
        """Path to execution_profile.yaml, or "" when the profile has none."""
        if self.exec_profile_path:
            return self.exec_profile_path
        from .profile import get_profile

        return getattr(get_profile(), "execution_profile_path", "") or ""

    def model_snapshot(self) -> dict[str, str]:
        """Chat + embed providers/models for audit / eval JSON reference."""
        return {
            "chat_provider": self.chat_provider,
            "chat_model": self.chat_model,
            "embed_provider": self.embed_provider,
            "embed_model": self.embed_model,
        }


settings = Settings()
