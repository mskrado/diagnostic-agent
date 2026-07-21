"""Extract token usage from LangChain LLM responses.

Providers surface usage differently (usage_metadata vs response_metadata).
This normalizes to a small dict suitable for audit JSONL / cost tracking.
"""
from __future__ import annotations

from typing import Any


def extract_token_usage(raw_msg: Any) -> dict[str, Any]:
    """Return {input_tokens, output_tokens, total_tokens, source} from an AIMessage.

    Missing values are null; source is usage_metadata | response_metadata | unavailable.
    """
    empty = {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "source": "unavailable",
    }
    if raw_msg is None:
        return empty

    # LangChain standardized path (OpenAI, many others, recent Bedrock)
    usage = getattr(raw_msg, "usage_metadata", None)
    if isinstance(usage, dict) and (
        usage.get("input_tokens") is not None or usage.get("output_tokens") is not None
    ):
        inp = usage.get("input_tokens")
        out = usage.get("output_tokens")
        total = usage.get("total_tokens")
        if total is None and inp is not None and out is not None:
            total = inp + out
        return {
            "input_tokens": inp,
            "output_tokens": out,
            "total_tokens": total,
            "source": "usage_metadata",
        }

    # Older / provider-specific nesting under response_metadata
    meta = getattr(raw_msg, "response_metadata", None) or {}
    if not isinstance(meta, dict):
        return empty

    for key in ("token_usage", "usage", "amazon-bedrock-invocationMetrics"):
        nested = meta.get(key)
        if not isinstance(nested, dict):
            continue
        inp = (
            nested.get("input_tokens")
            or nested.get("prompt_tokens")
            or nested.get("inputTokenCount")
        )
        out = (
            nested.get("output_tokens")
            or nested.get("completion_tokens")
            or nested.get("outputTokenCount")
        )
        total = nested.get("total_tokens") or nested.get("totalTokenCount")
        if total is None and inp is not None and out is not None:
            try:
                total = int(inp) + int(out)
            except (TypeError, ValueError):
                total = None
        if inp is not None or out is not None:
            return {
                "input_tokens": inp,
                "output_tokens": out,
                "total_tokens": total,
                "source": "response_metadata",
            }

    return empty
