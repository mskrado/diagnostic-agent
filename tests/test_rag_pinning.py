"""Whole-runbook pinning by alert name."""
from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.rag.pinning import build_alert_index, parse_alert_names, pinned_docs
from app.rag.store import RagStore

_RUNBOOKS = Path(__file__).resolve().parents[1] / "runbooks"


def test_parse_bullet_list_header():
    text = (
        "# Runbook\n\n**Alerts:**\n"
        "- `HostDiskSpaceLow` — `node_filesystem_avail_bytes / size < 0.10`\n"
        "- `HostDiskFillPredicted` — `predict_linear(...[6h], 24h) < 0`\n\n"
        "## Meaning\n`NotAnAlertHeader` mention\n"
    )
    assert parse_alert_names(text) == {"HostDiskSpaceLow", "HostDiskFillPredicted"}


def test_parse_inline_header_skips_queries_and_services():
    text = (
        "# Runbook\n\n**Alert:** `PostgresErrorsInLogs` — Loki rate of "
        '`{service="platform-service"} |~ "(?i)postgres"` > 0.1 on `platform-service`.\n'
    )
    assert parse_alert_names(text) == {"PostgresErrorsInLogs"}


def test_parse_multiline_prose_header():
    text = (
        "**Alert:** Prefer dedicated alerts `SmtpConnectionFailures` and\n"
        "`SmtpMailpitFallback`. Legacy noise under\n`ExternalApiErrorsInLogs`.\n\n"
        "## Meaning\n"
    )
    assert parse_alert_names(text) == {
        "SmtpConnectionFailures",
        "SmtpMailpitFallback",
        "ExternalApiErrorsInLogs",
    }


def test_template_placeholder_is_not_an_alert():
    assert parse_alert_names("**Alert:** `<PromQL or Loki query>` for `<duration>`.") == set()


def test_generic_alert_named_by_many_runbooks_is_not_pinned():
    index = build_alert_index(
        [f"**Alert:** `ExternalDependencyErrors` — {n}\n" for n in ("twilio", "s3", "openai")]
    )
    assert pinned_docs(index, "ExternalDependencyErrors", max_docs=2, max_chars=6000) == []


def test_pinned_doc_truncated_to_budget():
    index = build_alert_index(["**Alert:** `BigAlert`\n\n" + "x" * 500])
    (doc,) = pinned_docs(index, "BigAlert", max_docs=2, max_chars=100)
    assert doc.endswith("…(truncated)")
    assert len(doc) < 130


def test_bundled_disk_runbook_indexed_for_all_disk_alerts():
    docs = [p.read_text(encoding="utf-8") for p in _RUNBOOKS.glob("*.md")]
    index = build_alert_index(docs)
    for alert in ("HostDiskSpaceLow", "HostDiskSpaceCritical", "HostDiskFillPredicted"):
        pinned = pinned_docs(index, alert, max_docs=2, max_chars=6000)
        assert len(pinned) == 1
        assert "docker system df" in pinned[0]


class _Doc:
    def __init__(self, content: str):
        self.page_content = content


class _FakeStore:
    def __init__(self, hits):
        self._hits = hits

    def similarity_search(self, text: str, k: int = 2):
        return [_Doc(h) for h in self._hits]


def test_query_many_prepends_pinned_and_skips_its_chunks(monkeypatch):
    monkeypatch.setattr(settings, "rag_pin_alert_runbooks", True)
    runbook = "**Alerts:**\n- `HostDiskFillPredicted`\n\n## First checks\ndocker system df"
    store = RagStore(
        _FakeStore(["## First checks\ndocker system df", "incident: overlay2 bloat"]),
        build_alert_index([runbook]),
    )
    ctx = store.query_many(["HostDiskFillPredicted host"], alert_type="HostDiskFillPredicted")
    parts = ctx.split("\n\n---\n\n")
    assert parts[0] == runbook
    assert parts[1:] == ["incident: overlay2 bloat"]


def test_query_many_pins_without_queries(monkeypatch):
    monkeypatch.setattr(settings, "rag_pin_alert_runbooks", True)
    store = RagStore(_FakeStore([]), build_alert_index(["**Alert:** `HostDiskSpaceLow`\nbody"]))
    assert "body" in store.query_many([], alert_type="HostDiskSpaceLow")


def test_pinning_can_be_disabled(monkeypatch):
    monkeypatch.setattr(settings, "rag_pin_alert_runbooks", False)
    store = RagStore(
        _FakeStore(["chunk"]), build_alert_index(["**Alert:** `HostDiskSpaceLow`\nbody"])
    )
    assert store.query_many(["q"], alert_type="HostDiskSpaceLow") == "chunk"
