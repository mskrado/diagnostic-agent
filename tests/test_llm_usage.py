"""Token usage extraction from LangChain AIMessage / provider metadata."""
from __future__ import annotations

from types import SimpleNamespace

from app.llm_usage import extract_token_usage


def test_usage_metadata_preferred():
    msg = SimpleNamespace(
        usage_metadata={"input_tokens": 100, "output_tokens": 40, "total_tokens": 140},
        response_metadata={"token_usage": {"prompt_tokens": 1, "completion_tokens": 1}},
    )
    u = extract_token_usage(msg)
    assert u["input_tokens"] == 100
    assert u["output_tokens"] == 40
    assert u["total_tokens"] == 140
    assert u["source"] == "usage_metadata"


def test_usage_metadata_computes_total():
    msg = SimpleNamespace(
        usage_metadata={"input_tokens": 10, "output_tokens": 5},
        response_metadata={},
    )
    u = extract_token_usage(msg)
    assert u["total_tokens"] == 15
    assert u["source"] == "usage_metadata"


def test_response_metadata_openai_style():
    msg = SimpleNamespace(
        usage_metadata=None,
        response_metadata={
            "token_usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70}
        },
    )
    u = extract_token_usage(msg)
    assert u["input_tokens"] == 50
    assert u["output_tokens"] == 20
    assert u["total_tokens"] == 70
    assert u["source"] == "response_metadata"


def test_response_metadata_bedrock_style():
    msg = SimpleNamespace(
        usage_metadata={},
        response_metadata={
            "amazon-bedrock-invocationMetrics": {
                "inputTokenCount": 200,
                "outputTokenCount": 30,
            }
        },
    )
    u = extract_token_usage(msg)
    assert u["input_tokens"] == 200
    assert u["output_tokens"] == 30
    assert u["total_tokens"] == 230
    assert u["source"] == "response_metadata"


def test_unavailable_when_missing():
    u = extract_token_usage(None)
    assert u["source"] == "unavailable"
    assert u["input_tokens"] is None
    assert u["output_tokens"] is None
    assert u["total_tokens"] is None
