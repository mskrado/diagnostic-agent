"""Multi-family RAG query building for mixed log samples."""
from __future__ import annotations

from app.rag.queries import build_rag_queries, classify_log_families
from app.rag.store import RagStore


def test_classify_mixed_sample_finds_postgres_redis_jvm():
    logs = [
        "OutOfMemoryError: Java heap space",
        "Connection to postgres:5432 refused",
        "RedisCommandTimeoutException: Command timed out",
        "High GC pause detected; heap used=1784MB",
        "Unable to connect to redis:6379",
    ]
    families = classify_log_families(logs)
    assert set(families) >= {"database", "cache", "jvm-memory"}
    assert any("postgres" in L.lower() for L in families["database"])
    assert any("redis" in L.lower() for L in families["cache"])


def test_build_rag_queries_one_per_family():
    logs = [
        "[ts] HikariPool: Exception during pool initialization postgres",
        "[ts] Lettuce: Unable to connect to Redis",
        "[ts] OutOfMemoryError: Java heap space",
    ]
    queries = build_rag_queries(
        alert_type="HighErrorRate",
        service="platform-service",
        log_lines=logs,
    )
    assert len(queries) == 3
    joined = " ".join(queries).lower()
    assert "database" in joined
    assert "cache" in joined
    assert "jvm-memory" in joined
    assert all("HighErrorRate" in q for q in queries)


def test_build_rag_queries_fallback_when_no_family():
    queries = build_rag_queries(
        alert_type="HighErrorRate",
        service="platform-service",
        log_lines=["something opaque happened"],
    )
    assert len(queries) == 1
    assert "opaque" in queries[0]


def test_query_many_dedupes_and_covers_families():
    class _Doc:
        def __init__(self, content: str):
            self.page_content = content

    class _FakeStore:
        def similarity_search(self, text: str, k: int = 2):
            t = text.lower()
            if "cache" in t or "redis" in t:
                return [_Doc("runbook redis connection errors lettuce")]
            if "jvm" in t or "heap" in t:
                return [_Doc("runbook jvm gc pressure heap oom")]
            if "database" in t or "postgres" in t:
                # Duplicate-ish content on second call should dedupe
                return [
                    _Doc("runbook postgres connectivity refused"),
                    _Doc("runbook postgres connectivity refused"),
                ]
            return [_Doc(f"generic for {text[:20]}")]

    store = RagStore(_FakeStore())
    queries = build_rag_queries(
        alert_type="HighErrorRate",
        service="platform-service",
        log_lines=[
            "postgres:5432 refused",
            "Unable to connect to redis:6379",
            "OutOfMemoryError: Java heap space",
        ],
    )
    ctx = store.query_many(queries, k_per_query=2, max_chunks=8)
    assert "redis" in ctx.lower()
    assert "jvm" in ctx.lower() or "heap" in ctx.lower()
    assert "postgres" in ctx.lower()
    # Deduped: postgres chunk once
    assert ctx.lower().count("runbook postgres connectivity refused") == 1
