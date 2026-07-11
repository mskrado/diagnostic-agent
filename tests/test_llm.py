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
