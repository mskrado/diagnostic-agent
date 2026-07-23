"""Runtime configuration, sourced from environment variables.

All defaults assume the agent runs inside the `publishi-network` Docker
network created by the observability overlay, so service DNS names (e.g.
`prometheus`, `loki`, `grafana`) resolve directly.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENT_", env_file=".env", extra="ignore")

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
    # JSON passthrough for provider-specific kwargs (base_url, region_name, …).
    chat_model_kwargs: str = "{}"
    embed_model_kwargs: str = "{}"

    # --- RAG ---
    rag_enabled: bool = True
    runbooks_path: str = str(_BASE_DIR / "runbooks")
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

    # SMTP diagnostic email (separate from Alertmanager alert email).
    email_enabled: bool = True
    email_to: str = "dev-alerts@localhost"
    email_subject_prefix: str = "publishi"
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_from: str = "diagnostic-agent@publishi.local"
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = False
    smtp_timeout: float = 15.0

    # --- Dependency map ---
    service_map_path: str = str(_BASE_DIR / "service_map.yaml")


settings = Settings()
