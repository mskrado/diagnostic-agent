"""Loki client log formatting."""
from __future__ import annotations

import json

from app.clients.loki import LokiClient


def test_format_log_entry_includes_timestamp_and_trace_id():
    line = json.dumps(
        {
            "@timestamp": "2026-07-07T06:58:12.345Z",
            "trace_id": "abc123def456",
            "logger_name": "com.publishi.health.OpenAIHealthIndicator",
            "message": "OpenAI health check failed: 401 Unauthorized",
            "level": "WARN",
        }
    )
    formatted = LokiClient._format_log_entry("1750000000000000000", line)
    assert "[2026-07-07T06:58:12.345Z]" in formatted
    assert "[trace_id=abc123def456]" in formatted
    assert "OpenAIHealthIndicator: OpenAI health check failed" in formatted


def test_format_log_entry_falls_back_to_loki_timestamp():
    line = "plain text log line"
    formatted = LokiClient._format_log_entry("1750000000000000000", line)
    assert "[trace_id=n/a]" in formatted
    assert "plain text log line" in formatted
    assert formatted.startswith("[")


def test_format_log_entries_batch():
    entries = [
        (
            "1750000000000000000",
            json.dumps(
                {
                    "@timestamp": "2026-07-07T06:58:12.345Z",
                    "trace_id": "t1",
                    "logger_name": "c.p.S3HealthIndicator",
                    "message": "S3 health check failed",
                }
            ),
        )
    ]
    out = LokiClient.format_log_entries(entries)
    assert len(out) == 1
    assert "[trace_id=t1]" in out[0]
    assert "S3HealthIndicator:" in out[0]


def test_query_range_sorts_newest_first_across_streams(monkeypatch):
    """Loki streams arrive unordered; sample[:N] must still be newest lines."""

    class _FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "result": [
                        {
                            "stream": {"service": "platform-service"},
                            "values": [
                                ["100", "older-health-noise"],
                                ["200", "mid-health-noise"],
                            ],
                        },
                        {
                            "stream": {"service": "platform-service", "blind_eval": "x"},
                            "values": [
                                ["300", "newest-injected"],
                                ["250", "newer-injected"],
                            ],
                        },
                    ]
                }
            }

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, *args, **kwargs):
            return _FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "Client", _FakeClient)
    client = LokiClient("http://loki:3100")
    entries = client.query_range('{service="platform-service"}', limit=10)
    assert [line for _ts, line in entries] == [
        "newest-injected",
        "newer-injected",
        "mid-health-noise",
        "older-health-noise",
    ]
