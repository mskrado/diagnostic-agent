"""RAG corpus quality checks — no live LLM/embeddings required."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

_RUNBOOKS = Path(__file__).resolve().parent.parent / "runbooks"
_CHUNK_SIZE = 800
_CHUNK_OVERLAP = 80
_TOP_K = 3


def _load_runbook_docs():
    loader = DirectoryLoader(
        str(_RUNBOOKS),
        glob="runbook-*.md",
        loader_cls=TextLoader,
        silent_errors=True,
    )
    return loader.load()


def test_runbook_corpus_not_empty():
    docs = _load_runbook_docs()
    assert len(docs) >= 5, "expected scaffold runbooks for RAG grounding"


def test_chunking_matches_store_defaults():
    docs = _load_runbook_docs()
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=_CHUNK_SIZE, chunk_overlap=_CHUNK_OVERLAP
    )
    chunks = splitter.split_documents(docs)
    assert chunks, "chunking produced no segments"
    assert all(len(c.page_content) <= _CHUNK_SIZE + 50 for c in chunks)


def test_keyword_retrieval_hits_db_pool_runbook():
    """Deterministic substring check — proxy for retrieval relevance."""
    docs = _load_runbook_docs()
    corpus = "\n".join(d.page_content.lower() for d in docs)
    assert "hikaricp" in corpus or "pool exhaust" in corpus


def test_rag_store_disabled_returns_empty():
    from app.rag.store import RagStore

    store = RagStore(None)
    assert store.available is False
    assert store.query("HighErrorRate 5xx spike") == ""


@pytest.mark.parametrize(
    "alert_snippet,expected_token",
    [
        ("redis connection timeout lettuce", "redis"),
        ("elasticsearch cluster yellow", "elasticsearch"),
        ("openai rate limit 429", "openai"),
    ],
)
def test_runbook_covers_dependency_alerts(alert_snippet, expected_token):
    docs = _load_runbook_docs()
    corpus = "\n".join(d.page_content.lower() for d in docs)
    assert expected_token in corpus
