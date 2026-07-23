"""LLM factory — LangChain init_chat_model / init_embeddings."""
from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

import app.llm as llm_mod
from app.config import settings


def test_kwargs_empty():
    assert llm_mod._kwargs("") == {}
    assert llm_mod._kwargs("  ") == {}


def test_kwargs_valid_json():
    assert llm_mod._kwargs('{"base_url": "http://ollama:11434"}') == {
        "base_url": "http://ollama:11434"
    }


def test_kwargs_invalid_json():
    assert llm_mod._kwargs("not-json") == {}


def test_kwargs_non_object():
    assert llm_mod._kwargs("[1, 2]") == {}


def test_get_chat_model_calls_init_chat_model(monkeypatch):
    monkeypatch.setattr(settings, "chat_provider", "openai")
    monkeypatch.setattr(settings, "chat_model", "gpt-4o-mini")
    monkeypatch.setattr(settings, "llm_temperature", 0.2)
    monkeypatch.setattr(settings, "chat_model_kwargs", '{"base_url": "https://api.example.com/v1"}')
    fake_model = MagicMock()
    with patch("app.llm.init_chat_model", return_value=fake_model) as init:
        result = llm_mod.get_chat_model()
    init.assert_called_once_with(
        "gpt-4o-mini",
        model_provider="openai",
        base_url="https://api.example.com/v1",
        temperature=0.2,
    )
    assert result is fake_model


def test_get_chat_model_bedrock_structured_forces_greedy_and_max_tokens(monkeypatch):
    monkeypatch.setattr(settings, "chat_provider", "bedrock_converse")
    monkeypatch.setattr(settings, "chat_model", "amazon.nova-pro-v1:0")
    monkeypatch.setattr(settings, "llm_temperature", 0.1)
    monkeypatch.setattr(settings, "chat_max_tokens", 8192)
    monkeypatch.setattr(settings, "chat_model_kwargs", '{"region_name": "us-east-1"}')
    with patch("app.llm.init_chat_model", return_value=MagicMock()) as init:
        llm_mod.get_chat_model(for_structured_output=True)
    assert init.call_args.kwargs["temperature"] == 0.0
    assert init.call_args.kwargs["max_tokens"] == 8192
    assert init.call_args.kwargs["region_name"] == "us-east-1"


def test_content_to_text_handles_bedrock_blocks_and_none():
    assert llm_mod.content_to_text("plain") == "plain"
    assert llm_mod.content_to_text(None) == ""
    # Block without a text key must not raise (regression: NoneType join).
    assert (
        llm_mod.content_to_text(
            [
                {"type": "reasoning"},
                {"type": "text", "text": "hello"},
                None,
                "world",
            ]
        )
        == "hello\nworld"
    )


def test_is_tooluse_model_error():
    assert llm_mod.is_tooluse_model_error(
        Exception(
            "An error occurred (ModelErrorException) when calling the Converse "
            "operation: Model produced invalid sequence as part of ToolUse."
        )
    )
    assert not llm_mod.is_tooluse_model_error(ValueError("unrelated"))


def test_invoke_structured_diagnosis_falls_back_on_tooluse_error(monkeypatch):
    from langchain_core.messages import AIMessage, HumanMessage

    from app.graph.schema import Diagnosis, Hypothesis

    structured = MagicMock()
    structured.invoke.side_effect = Exception(
        "ModelErrorException: Model produced invalid sequence as part of ToolUse"
    )
    diagnosis = Diagnosis(
        primary_hypothesis=Hypothesis(cause="db down", confidence=80, evidence="refused"),
        blast_radius_assessment="all",
        suggested_next_steps=["check postgres"],
        confidence_note="medium",
    )
    base = MagicMock()
    base.invoke.return_value = AIMessage(content=diagnosis.model_dump_json())

    monkeypatch.setattr(llm_mod, "get_chat_model", lambda **kwargs: base)
    out = llm_mod.invoke_structured_diagnosis(
        structured, [HumanMessage(content="diagnose")]
    )
    assert out["parsed"].primary_hypothesis.cause == "db down"
    assert out["parsing_error"] is None
    base.invoke.assert_called_once()


def test_get_chat_model_merges_temperature_default(monkeypatch):
    monkeypatch.setattr(settings, "chat_provider", "ollama")
    monkeypatch.setattr(settings, "chat_model", "mistral:7b-instruct")
    monkeypatch.setattr(settings, "llm_temperature", 0.1)
    monkeypatch.setattr(settings, "chat_model_kwargs", "{}")
    with patch("app.llm.init_chat_model", return_value=MagicMock()) as init:
        llm_mod.get_chat_model()
    assert init.call_args.kwargs["temperature"] == 0.1


def test_get_embeddings_calls_init_embeddings(monkeypatch):
    monkeypatch.setattr(settings, "embed_provider", "ollama")
    monkeypatch.setattr(settings, "embed_model", "nomic-embed-text")
    monkeypatch.setattr(
        settings, "embed_model_kwargs", '{"base_url": "http://ollama:11434"}'
    )
    fake_emb = MagicMock()
    with patch("app.llm.init_embeddings", return_value=fake_emb) as init:
        result = llm_mod.get_embeddings()
    init.assert_called_once_with(
        "nomic-embed-text",
        provider="ollama",
        base_url="http://ollama:11434",
    )
    assert result is fake_emb
