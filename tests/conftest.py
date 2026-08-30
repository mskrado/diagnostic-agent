"""Pytest defaults — pin the Spring modular-monolith example for regression tests.

Individual tests may override via ``build_profile`` / env + ``reset_profile_cache``.

Also holds the fake observability stack (``fake_stack``) used by both the scan
and draft suites, so there is one definition of what a stack looks like.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SPRING_EXAMPLE = _ROOT / "examples" / "spring-modular-monolith"


@pytest.fixture(autouse=True)
def _pin_spring_example_profile(monkeypatch):
    monkeypatch.setenv("AGENT_PROFILE_DIR", str(_SPRING_EXAMPLE))
    monkeypatch.setenv("AGENT_DEFAULT_PRESET", "spring-micrometer")
    # Clear any empty overrides so profile paths resolve from the profile dir.
    monkeypatch.delenv("AGENT_SERVICE_MAP_PATH", raising=False)
    monkeypatch.delenv("AGENT_RUNBOOKS_PATH", raising=False)

    # Settings + profile caches may already have been constructed at import time.
    from app import config as config_mod
    from app.delivery.redact import reset_redaction_cache
    from app.profile import reset_profile_cache

    config_mod.settings = config_mod.Settings()
    reset_profile_cache()
    reset_redaction_cache()
    yield
    reset_profile_cache()
    reset_redaction_cache()


# ---------------------------------------------------------------------------
# A fake Prometheus + Loki + Alertmanager, for `diag scan` and `diag draft`
# ---------------------------------------------------------------------------
_JSON_LOG_LINE = json.dumps(
    {
        "@timestamp": "2026-08-30T10:00:00Z",
        "level": "ERROR",
        "logger_name": "com.example.platform.media.MediaService",
        "message": "upload failed for ops@example.com tenant_id=tenant-9",
    }
)


@pytest.fixture
def json_log_line() -> str:
    """One realistic JSON log line, carrying an email and a tenant id."""
    return _JSON_LOG_LINE


def prometheus_payloads() -> dict:
    return {
        "/api/v1/label/__name__/values": [
            "up",
            "http_server_requests_seconds_count",
            "hikaricp_connections_pending",
        ],
        "/api/v1/status/buildinfo": {"version": "2.51.0"},
        "/api/v1/labels": ["__name__", "job", "service"],
        "/api/v1/label/service/values": ["api-gateway", "platform-service", "postgres"],
        "/api/v1/label/job/values": ["platform-service", "prometheus"],
        "/api/v1/targets": {
            "activeTargets": [
                {
                    "health": "up",
                    "labels": {
                        "job": "platform-service",
                        "instance": "platform-service:8080",
                        "service": "platform-service",
                    },
                }
            ]
        },
        "/api/v1/rules": {
            "groups": [
                {
                    "name": "app",
                    "rules": [
                        {
                            "type": "alerting",
                            "name": "HighErrorRate",
                            "query": 'rate(http_server_requests_seconds_count{status=~"5.."}[5m]) > 1',
                            "duration": 300,
                            "labels": {"severity": "critical"},
                            "annotations": {"runbook": "runbook-high-error-rate.md"},
                        }
                    ],
                }
            ]
        },
    }


class StubResponse:
    def __init__(self, payload=None, text=""):
        self._payload = payload
        self.text = text
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _spring_promql_returns(query: str) -> bool:
    """Which instant queries have data on the fake stack.

    Client-side metrics answer only for the monolith: a stack where every
    service looks like it talks to every dependency would let a broken edge
    inference pass unnoticed.
    """
    if "hikaricp" in query or "lettuce" in query:
        return "platform-service" in query
    return (
        "http_server_requests_seconds_count" in query
        or query.startswith("up{")
        or "jvm_memory_used_bytes" in query
    )


@pytest.fixture
def fake_stack(monkeypatch):
    """Install a fake stack over httpx and return the installer.

    Dispatch is prefix-first: Loki's ``/loki/api/v1/labels`` also ends with
    Prometheus's ``/api/v1/labels``, so suffix matching alone would answer Loki
    with Prometheus payloads.
    """

    def install(*, loki_lines=None, loki_labels=None, promql_returns=None):
        prom = prometheus_payloads()
        labels = loki_labels if loki_labels is not None else ["service", "level"]
        lines = loki_lines if loki_lines is not None else []
        answers = promql_returns or _spring_promql_returns

        def handler(url, params=None):
            if "/loki/api/v1/" not in url and "/api/v2/" not in url:
                if url.endswith("/api/v1/query"):
                    query = (params or {}).get("query", "")
                    result = (
                        [{"metric": {}, "value": [0, "1"]}] if answers(query) else []
                    )
                    return StubResponse({"data": {"result": result}})
                for path, payload in prom.items():
                    if url.endswith(path):
                        return StubResponse({"data": payload})
                raise AssertionError(f"unexpected prometheus url {url}")
            if url.endswith("/loki/api/v1/labels"):
                return StubResponse({"data": labels})
            if url.endswith("/loki/api/v1/label/service/values"):
                return StubResponse({"data": ["api-gateway", "platform-service"]})
            if url.endswith("/loki/api/v1/rules"):
                return StubResponse(
                    text=(
                        "ns:\n"
                        "  - name: log-alerts\n"
                        "    rules:\n"
                        "      - alert: PostgresErrorsInLogs\n"
                        '        expr: sum(count_over_time({service="platform-service"} '
                        '|~ "(?i)(postgres|jdbc)" [5m])) > 0\n'
                        "        labels:\n"
                        "          severity: warning\n"
                    )
                )
            if url.endswith("/loki/api/v1/query_range"):
                query = (params or {}).get("query", "")
                if "|~" in query:
                    # Dependency-name probe: postgres errors land in the app stream.
                    return StubResponse(
                        {
                            "data": {
                                "result": [
                                    {
                                        "stream": {"service": "platform-service"},
                                        "values": [["3", "jdbc connection refused"]],
                                    }
                                ]
                            }
                        }
                    )
                value = (
                    "platform-service" if "platform-service" in query else "api-gateway"
                )
                return StubResponse(
                    {
                        "data": {
                            "result": [
                                {
                                    "stream": {"service": value},
                                    "values": [
                                        [str(i + 1), line]
                                        for i, line in enumerate(lines)
                                    ],
                                }
                            ]
                        }
                    }
                )
            if url.endswith("/api/v2/status"):
                return StubResponse(
                    {
                        "versionInfo": {"version": "0.27.0"},
                        "config": {
                            "original": "receivers:\n- slack_configs:\n"
                            "  - api_url: https://hooks.slack.com/T00/B00/SUPERSECRET\n"
                        },
                    }
                )
            if url.endswith("/api/v2/receivers"):
                return StubResponse([{"name": "diagnostic-agent"}])
            if url.endswith("/api/v2/alerts"):
                return StubResponse([{"labels": {"alertname": "HighErrorRate"}}])
            raise AssertionError(f"unexpected url {url} params={params}")

        class _Client:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url, params=None):
                return handler(url, params)

        monkeypatch.setattr(httpx, "Client", _Client)

    return install
