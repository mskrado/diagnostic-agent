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
import re
from typing import Any

from langchain.chat_models import init_chat_model
from langchain.embeddings import init_embeddings
from langchain_core.messages import HumanMessage, SystemMessage

from .config import settings
from .graph.schema import Diagnosis

logger = logging.getLogger(__name__)

_TOOLUSE_ERROR_MARKERS = (
    "invalid sequence as part of tooluse",
    "modelerrorexception",
    "tooluse",
)


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


def _is_bedrock_provider(provider: str | None) -> bool:
    return "bedrock" in (provider or "").lower()


def get_chat_model(*, for_structured_output: bool = False):
    """Return a LangChain chat model for the configured provider/model.

    For Bedrock Converse structured output (ToolUse), AWS recommends greedy
    decoding (temperature=0) and a high maxTokens budget so large Diagnosis
    payloads are not truncated mid-tool-call.
    """
    kwargs = _kwargs(settings.chat_model_kwargs)
    provider = settings.chat_provider
    if for_structured_output and _is_bedrock_provider(provider):
        kwargs["temperature"] = 0.0
        kwargs.setdefault("max_tokens", settings.chat_max_tokens)
    else:
        kwargs.setdefault("temperature", settings.llm_temperature)
        if _is_bedrock_provider(provider):
            kwargs.setdefault("max_tokens", settings.chat_max_tokens)
    logger.info(
        "Chat model: provider=%s model=%s temperature=%s max_tokens=%s structured=%s",
        provider,
        settings.chat_model,
        kwargs.get("temperature"),
        kwargs.get("max_tokens"),
        for_structured_output,
    )
    return init_chat_model(
        settings.chat_model,
        model_provider=provider,
        **kwargs,
    )


def get_structured_diagnosis_llm():
    """Chat model bound to the Diagnosis schema (include_raw for token audit)."""
    return get_chat_model(for_structured_output=True).with_structured_output(
        Diagnosis, include_raw=True
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


def content_to_text(content: Any) -> str:
    """Flatten a chat message ``content`` (str or Bedrock block list) to text.

    Bedrock Converse can return a list of content blocks, some of which
    (reasoning, tool_use) have no ``text`` key. Coerce every element to a
    string so ``"\\n".join`` never sees ``None``.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if text:
                    parts.append(str(text))
            elif block is not None:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


def is_tooluse_model_error(exc: BaseException) -> bool:
    """True when Bedrock Nova rejects / truncates a ToolUse structured call."""
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _TOOLUSE_ERROR_MARKERS)


def _extract_json_object(text: str) -> str:
    """Best-effort extract of a top-level JSON object from model text."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty model response")
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if fence:
        raw = fence.group(1).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object found in model response")
    return raw[start : end + 1]


def _diagnosis_json_fallback(messages: list) -> dict:
    """Plain-chat JSON parse when Converse ToolUse fails (Nova ModelErrorException)."""
    base = get_chat_model(for_structured_output=True)
    fallback_messages = list(messages) + [
        HumanMessage(
            content=(
                "IMPORTANT: The structured tool call failed. Respond with ONLY a "
                "single JSON object matching the Diagnosis schema from the system "
                "prompt (issue_categories, primary_hypothesis, secondary_hypotheses, "
                "blast_radius_assessment, suggested_next_steps, tool_run_examples, "
                "fix_suggestions, confidence_note). No markdown fences, no prose."
            )
        )
    ]
    raw_msg = base.invoke(fallback_messages)
    content = content_to_text(getattr(raw_msg, "content", ""))
    try:
        payload = json.loads(_extract_json_object(content))
        parsed = Diagnosis.model_validate(payload)
        return {"parsed": parsed, "raw": raw_msg, "parsing_error": None}
    except Exception as parse_exc:  # noqa: BLE001
        logger.warning("JSON fallback parse failed: %s", parse_exc)
        return {"parsed": None, "raw": raw_msg, "parsing_error": parse_exc}


def invoke_structured_diagnosis(structured_llm, messages: list) -> dict:
    """Invoke Diagnosis structured output; fall back to JSON on Nova ToolUse errors.

    Returns the same shape as ``with_structured_output(..., include_raw=True)``:
    ``{"parsed", "raw", "parsing_error"}``.
    """
    try:
        result = structured_llm.invoke(messages)
        if isinstance(result, dict):
            return result
        # Some bindings return the pydantic model directly
        return {"parsed": result, "raw": None, "parsing_error": None}
    except Exception as exc:  # noqa: BLE001
        if not is_tooluse_model_error(exc):
            raise
        logger.warning(
            "Bedrock ToolUse structured output failed (%s); retrying via JSON fallback",
            exc,
        )
        return _diagnosis_json_fallback(messages)
