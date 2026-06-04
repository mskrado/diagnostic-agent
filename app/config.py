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

    # --- LLM provider: "ollama" (default, on-prem) or "openai" (fallback) ---
    llm_provider: str = "ollama"
    llm_temperature: float = 0.1

    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "mistral:7b-instruct"
    ollama_embed_model: str = "nomic-embed-text"

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_embed_model: str = "text-embedding-3-small"

    # --- RAG ---
    rag_enabled: bool = True
    runbooks_path: str = str(_BASE_DIR / "runbooks")
    chroma_path: str = str(_BASE_DIR / "chroma_db")
    rag_top_k: int = 3

    # --- Retrieval tuning ---
    loki_lookback_minutes: int = 15
    loki_limit: int = 500
    metrics_window: str = "5m"

    # --- Delivery ---
    audit_log_dir: str = str(_BASE_DIR / "audit")
    grafana_annotations_enabled: bool = True

    # --- Dependency map ---
    service_map_path: str = str(_BASE_DIR / "service_map.yaml")


settings = Settings()
