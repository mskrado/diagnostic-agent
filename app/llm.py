"""Pluggable LLM + embeddings factory.

Chat and embedding backends are selected purely via environment variables
(AGENT_CHAT_PROVIDER / AGENT_CHAT_MODEL / AGENT_EMBED_PROVIDER / …).
LangChain's universal factories handle provider wiring; credentials are read
from standard SDK env vars (OPENAI_API_KEY, ANTHROPIC_API_KEY, AWS credential
chain, etc.).

To add a new provider: install its langchain-* package and set the env vars.
No code changes required.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain.chat_models import init_chat_model
from langchain.embeddings import init_embeddings

from .config import settings

logger = logging.getLogger(__name__)


def _kwargs(raw: str) -> dict[str, Any]:
    """Parse a JSON kwargs string; empty or invalid -> {}."""
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("Invalid model kwargs JSON %r: %s; using {}", text, exc)
        return {}
    if not isinstance(parsed, dict):
        logger.warning("Model kwargs must be a JSON object; got %s; using {}", type(parsed))
        return {}
    return parsed


def get_chat_model():
    """Return a LangChain chat model for the configured provider/model."""
    kwargs = _kwargs(settings.chat_model_kwargs)
    kwargs.setdefault("temperature", settings.llm_temperature)
    logger.info(
        "Chat model: provider=%s model=%s",
        settings.chat_provider,
        settings.chat_model,
    )
    return init_chat_model(
        settings.chat_model,
        model_provider=settings.chat_provider,
        **kwargs,
    )


def get_embeddings():
    """Return a LangChain embeddings model for the configured provider/model."""
    kwargs = _kwargs(settings.embed_model_kwargs)
    logger.info(
        "Embeddings: provider=%s model=%s",
        settings.embed_provider,
        settings.embed_model,
    )
    return init_embeddings(
        settings.embed_model,
        provider=settings.embed_provider,
        **kwargs,
    )
