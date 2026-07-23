"""RAG store rebuilds when Chroma embed dimensions disagree."""
from __future__ import annotations

import pytest

from app.rag import store as store_mod


def test_chroma_from_chunks_wipes_on_dimension_mismatch(tmp_path, monkeypatch):
    calls: list[str] = []

    class _FakeChroma:
        @classmethod
        def from_documents(cls, chunks, embedding=None, persist_directory=None):
            calls.append(persist_directory)
            if len(calls) == 1:
                raise ValueError(
                    "Collection expecting embedding with dimension of 1536, got 1024"
                )
            return {"ok": True, "n": len(chunks), "path": persist_directory}

    monkeypatch.setattr(
        "langchain_chroma.Chroma",
        _FakeChroma,
        raising=False,
    )
    # Import path used inside helper
    import langchain_chroma

    monkeypatch.setattr(langchain_chroma, "Chroma", _FakeChroma)

    persist = tmp_path / "chroma"
    persist.mkdir()
    (persist / "stale.bin").write_text("old", encoding="utf-8")

    out = store_mod._chroma_from_chunks(
        [{"page_content": "postgres runbook"}],
        embeddings=object(),
        persist_directory=str(persist),
    )
    assert out["ok"] is True
    assert len(calls) == 2
    assert not (persist / "stale.bin").exists()


def test_chroma_from_chunks_reraises_unrelated_errors(tmp_path, monkeypatch):
    class _FakeChroma:
        @classmethod
        def from_documents(cls, *args, **kwargs):
            raise RuntimeError("disk full")

    import langchain_chroma

    monkeypatch.setattr(langchain_chroma, "Chroma", _FakeChroma)

    with pytest.raises(RuntimeError, match="disk full"):
        store_mod._chroma_from_chunks([], embeddings=object(), persist_directory=str(tmp_path))
