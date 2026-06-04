"""Pluggable LLM + embeddings factory.

Default provider is Ollama (fully on-prem, zero external calls -- the headline
use case). Setting AGENT_LLM_PROVIDER=openai swaps in OpenAI, reusing the
platform's existing OPENAI_API_KEY for fast iteration / CI where a local GPU
isn't available.

LangChain chat/embedding objects are imported lazily so that installing only
one provider's extras still works.
"""
from __future__ import annotations

import logging

from .config import settings

logger = logging.getLogger(__name__)


def get_chat_model():
    """Return a LangChain chat model configured for JSON output, temp from env."""
    provider = settings.llm_provider.lower()
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        logger.info("LLM provider: openai (%s)", settings.openai_model)
        return ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            temperature=settings.llm_temperature,
            model_kwargs={"response_format": {"type": "json_object"}},
        )

    # default: ollama
    from langchain_ollama import ChatOllama

    logger.info("LLM provider: ollama (%s)", settings.ollama_model)
    return ChatOllama(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
        temperature=settings.llm_temperature,
        format="json",
    )


def get_embeddings():
    """Return a LangChain embeddings model matching the active provider."""
    provider = settings.llm_provider.lower()
    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(
            model=settings.openai_embed_model, api_key=settings.openai_api_key
        )

    from langchain_ollama import OllamaEmbeddings

    return OllamaEmbeddings(
        model=settings.ollama_embed_model, base_url=settings.ollama_base_url
    )
