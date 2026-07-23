"""Local RAG store over runbooks + past incidents.

Embeddings run via the active LLM provider (Ollama by default). The store is
built ONCE at startup (not per-alert) and persisted to disk. Everything here
degrades gracefully: if RAG is disabled or the corpus is empty, the agent
simply runs without retrieved context.
"""
from __future__ import annotations

import logging
import os

from ..config import settings
from ..llm import get_embeddings

logger = logging.getLogger(__name__)


class RagStore:
    def __init__(self, store):
        self._store = store

    @property
    def available(self) -> bool:
        return self._store is not None

    def query(self, text: str, k: int | None = None) -> str:
        """Single-query retrieval (kept for simple call sites / tests)."""
        if not self.available:
            return ""
        k = k or settings.rag_top_k
        try:
            results = self._store.similarity_search(text, k=k)
        except Exception as exc:  # noqa: BLE001 - never let RAG break a diagnosis
            logger.warning("RAG query failed: %s", exc)
            return ""
        return "\n\n---\n\n".join(d.page_content for d in results)

    def query_many(
        self,
        queries: list[str],
        *,
        k_per_query: int | None = None,
        max_chunks: int | None = None,
    ) -> str:
        """Retrieve for each query and merge unique chunks (mixed-error samples).

        Dedupes by page_content so repeated postgres hits do not crowd out
        redis/jvm runbooks when several families are present.
        """
        if not self.available or not queries:
            return ""
        k = k_per_query if k_per_query is not None else settings.rag_top_k
        cap = max_chunks if max_chunks is not None else settings.rag_max_chunks
        seen: set[str] = set()
        merged: list[str] = []
        for q in queries:
            q = (q or "").strip()
            if not q:
                continue
            try:
                hits = self._store.similarity_search(q, k=k)
            except Exception as exc:  # noqa: BLE001
                logger.warning("RAG query failed (%r): %s", q[:80], exc)
                continue
            for doc in hits:
                content = (doc.page_content or "").strip()
                if not content or content in seen:
                    continue
                seen.add(content)
                merged.append(content)
                if len(merged) >= cap:
                    return "\n\n---\n\n".join(merged)
        return "\n\n---\n\n".join(merged)


def build_rag_store() -> RagStore:
    """Load (or build) the persisted Chroma store from the runbooks folder."""
    if not settings.rag_enabled:
        logger.info("RAG disabled (AGENT_RAG_ENABLED=false)")
        return RagStore(None)

    try:
        from langchain_chroma import Chroma
        from langchain_community.document_loaders import DirectoryLoader, TextLoader
        from langchain_text_splitters import RecursiveCharacterTextSplitter
    except ImportError as exc:
        logger.warning("RAG deps missing (%s); continuing without RAG", exc)
        return RagStore(None)

    runbooks = settings.runbooks_path
    if not os.path.isdir(runbooks):
        logger.warning("Runbooks path %s missing; RAG empty", runbooks)
        return RagStore(None)

    try:
        embeddings = get_embeddings()
        loader = DirectoryLoader(
            runbooks, glob="**/*.md", loader_cls=TextLoader, silent_errors=True
        )
        docs = loader.load()
        if not docs:
            logger.warning("No runbook docs found in %s; RAG empty", runbooks)
            return RagStore(None)
        splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=80)
        chunks = splitter.split_documents(docs)
        store = _chroma_from_chunks(chunks, embeddings, settings.chroma_path)
        logger.info("RAG store built: %d chunks from %d docs", len(chunks), len(docs))
        return RagStore(store)
    except Exception as exc:  # noqa: BLE001 - startup must not crash on RAG
        logger.warning("Failed to build RAG store: %s; continuing without RAG", exc)
        return RagStore(None)


def _chroma_from_chunks(chunks, embeddings, persist_directory: str):
    """Build Chroma; on embed-dim mismatch wipe the persist dir and rebuild.

    Switching providers (e.g. OpenAI 1536 → Titan 1024) leaves an incompatible
    on-disk collection; Chroma then fails and the agent would silently run
    without runbooks.
    """
    import shutil

    from langchain_chroma import Chroma

    try:
        return Chroma.from_documents(
            chunks, embedding=embeddings, persist_directory=persist_directory
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "dimension" not in msg and "expecting embedding" not in msg:
            raise
        logger.warning(
            "Chroma embed dimension mismatch (%s); wiping %s and rebuilding",
            exc,
            persist_directory,
        )
        if os.path.isdir(persist_directory):
            shutil.rmtree(persist_directory)
        os.makedirs(persist_directory, exist_ok=True)
        return Chroma.from_documents(
            chunks, embedding=embeddings, persist_directory=persist_directory
        )
