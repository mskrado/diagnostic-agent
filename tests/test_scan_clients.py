"""Discovery methods on the Prometheus, Loki, and Alertmanager clients.

Every method must degrade to an empty result rather than raise: a scan of a
half-reachable stack is still worth reading.
"""
from __future__ import annotations

import httpx
import pytest

from app.clients.alertmanager import AlertmanagerClient
from app.clients.loki import LokiClient
from app.clients.prometheus import PrometheusClient


class _Resp:
    def __init__(self, payload=None, *, text="", status_code=200):
        self._payload = payload
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=None
            )

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _fake_httpx(monkeypatch, handler):
    """Route every httpx GET through ``handler(url, params)``."""
    calls: list[tuple[str, dict | None]] = []

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, params=None):
            calls.append((url, params))
            return handler(url, params)

    monkeypatch.setattr(httpx, "Client", _Client)
    return calls


# -- Prometheus --------------------------------------------------------------
def test_prometheus_discovery_methods(monkeypatch):
    def handler(url, params):
        if url.endswith("/api/v1/labels"):
            return _Resp({"data": ["__name__", "job", "service"]})
        if url.endswith("/api/v1/label/service/values"):
            return _Resp({"data": ["api", "app"]})
        if url.endswith("/api/v1/label/__name__/values"):
            return _Resp({"data": ["up", "http_requests_total"]})
        if url.endswith("/api/v1/status/buildinfo"):
            return _Resp({"data": {"version": "2.51.0"}})
        if url.endswith("/api/v1/metadata"):
            return _Resp(
                {"data": {"up": [{"type": "gauge", "help": "target up"}], "bad": "x"}}
            )
        if url.endswith("/api/v1/series"):
            return _Resp({"data": [{"__name__": "up", "service": "app"}]})
        raise AssertionError(f"unexpected url {url}")

    _fake_httpx(monkeypatch, handler)
    client = PrometheusClient("http://prometheus:9090")

    assert client.labels() == ["__name__", "job", "service"]
    assert client.label_values("service") == ["api", "app"]
    assert client.metric_names() == ["up", "http_requests_total"]
    assert client.build_info() == {"version": "2.51.0"}
    # Non-list metadata entries are dropped rather than crashing the scan.
    assert client.metadata() == {"up": [{"type": "gauge", "help": "target up"}]}
    assert client.series(['{service="app"}']) == [{"__name__": "up", "service": "app"}]


def test_prometheus_series_skips_request_without_match(monkeypatch):
    calls = _fake_httpx(monkeypatch, lambda url, params: _Resp({"data": []}))
    assert PrometheusClient("http://prometheus:9090").series([]) == []
    assert calls == []


def test_prometheus_targets_and_rules(monkeypatch):
    def handler(url, params):
        if url.endswith("/api/v1/targets"):
            return _Resp(
                {
                    "data": {
                        "activeTargets": [
                            {
                                "health": "up",
                                "labels": {
                                    "job": "app",
                                    "instance": "app:8080",
                                    "service": "app",
                                },
                            },
                            "not-a-dict",
                        ],
                        "droppedTargets": [{"health": "unknown", "labels": {}}],
                    }
                }
            )
        if url.endswith("/api/v1/rules"):
            return _Resp(
                {
                    "data": {
                        "groups": [
                            {
                                "name": "app",
                                "rules": [
                                    {
                                        "type": "alerting",
                                        "name": "HighErrorRate",
                                        "query": "rate(x[5m]) > 1",
                                        "labels": {"severity": "critical"},
                                    },
                                    {"type": "recording", "name": "job:x"},
                                ],
                            }
                        ]
                    }
                }
            )
        raise AssertionError(f"unexpected url {url}")

    _fake_httpx(monkeypatch, handler)
    client = PrometheusClient("http://prometheus:9090")

    targets = client.targets()
    assert len(targets) == 1
    assert targets[0]["labels"]["job"] == "app"
    assert len(client.targets(state="dropped")) == 1
    assert client.rules()[0]["name"] == "app"


@pytest.mark.parametrize(
    "method,args",
    [
        ("labels", ()),
        ("label_values", ("service",)),
        ("metric_names", ()),
        ("targets", ()),
        ("rules", ()),
        ("metadata", ()),
        ("build_info", ()),
    ],
)
def test_prometheus_discovery_degrades_on_error(monkeypatch, method, args):
    def handler(url, params):
        raise httpx.ConnectError("refused")

    _fake_httpx(monkeypatch, handler)
    result = getattr(PrometheusClient("http://prometheus:9090"), method)(*args)
    assert result in ([], {}, "")


# -- Loki --------------------------------------------------------------------
def test_loki_discovery_methods(monkeypatch):
    def handler(url, params):
        if url.endswith("/loki/api/v1/labels"):
            return _Resp({"data": ["service", "level"]})
        if url.endswith("/loki/api/v1/label/service/values"):
            return _Resp({"data": ["app", "api"]})
        if url.endswith("/loki/api/v1/series"):
            return _Resp({"data": [{"service": "app"}]})
        raise AssertionError(f"unexpected url {url}")

    _fake_httpx(monkeypatch, handler)
    client = LokiClient("http://loki:3100")

    assert client.labels() == ["service", "level"]
    assert client.label_values("service") == ["app", "api"]
    assert client.series(['{service="app"}']) == [{"service": "app"}]


def test_loki_rules_parses_yaml(monkeypatch):
    """The ruler API answers YAML, unlike every other Loki endpoint."""
    yaml_body = """
namespace1:
  - name: log-alerts
    rules:
      - alert: PostgresErrorsInLogs
        expr: |
          sum(count_over_time({service="app"} |~ "(?i)(postgres|jdbc).*(refused)" [5m])) > 0
        for: 5m
        labels:
          severity: warning
"""
    _fake_httpx(monkeypatch, lambda url, params: _Resp(text=yaml_body))
    rules = LokiClient("http://loki:3100").rules()
    assert list(rules) == ["namespace1"]
    assert rules["namespace1"][0]["rules"][0]["alert"] == "PostgresErrorsInLogs"


def test_loki_rules_treats_404_as_ruler_disabled(monkeypatch):
    _fake_httpx(monkeypatch, lambda url, params: _Resp(text="", status_code=404))
    assert LokiClient("http://loki:3100").rules() == {}


def test_loki_query_range_streams_keeps_stream_labels(monkeypatch):
    payload = {
        "data": {
            "result": [
                {
                    "stream": {"service": "app"},
                    "values": [["2", "jdbc timeout"], ["1", "hikari exhausted"]],
                },
                {"stream": "malformed", "values": [["3", "ignored"]]},
            ]
        }
    }
    _fake_httpx(monkeypatch, lambda url, params: _Resp(payload))
    streams = LokiClient("http://loki:3100").query_range_streams('{service=~".+"}')
    assert len(streams) == 1
    labels, lines = streams[0]
    assert labels == {"service": "app"}
    assert lines == ["jdbc timeout", "hikari exhausted"]


def test_loki_query_range_streams_degrades(monkeypatch):
    def handler(url, params):
        raise httpx.ConnectError("refused")

    _fake_httpx(monkeypatch, handler)
    assert LokiClient("http://loki:3100").query_range_streams("{}") == []


# -- Alertmanager ------------------------------------------------------------
def test_alertmanager_version_receivers_and_firing(monkeypatch):
    def handler(url, params):
        if url.endswith("/api/v2/status"):
            return _Resp(
                {
                    "versionInfo": {"version": "0.27.0"},
                    # Must never surface: contains webhook URLs with tokens.
                    "config": {"original": "receivers:\n- name: slack\n  url: secret"},
                }
            )
        if url.endswith("/api/v2/receivers"):
            return _Resp([{"name": "slack"}, {"name": "agent"}, {}])
        if url.endswith("/api/v2/alerts"):
            return _Resp(
                [
                    {"labels": {"alertname": "HighErrorRate"}},
                    {"labels": {"alertname": "HighErrorRate"}},
                    {"labels": {"alertname": "DiskPressure"}},
                    {"labels": "malformed"},
                    {},
                ]
            )
        raise AssertionError(f"unexpected url {url}")

    _fake_httpx(monkeypatch, handler)
    client = AlertmanagerClient("http://alertmanager:9093")

    assert client.version() == "0.27.0"
    assert client.receivers() == ["slack", "agent"]
    assert client.firing_alertnames() == {"HighErrorRate": 2, "DiskPressure": 1}


def test_alertmanager_degrades_on_error(monkeypatch):
    def handler(url, params):
        raise httpx.ConnectError("refused")

    _fake_httpx(monkeypatch, handler)
    client = AlertmanagerClient("http://alertmanager:9093")
    assert client.version() == ""
    assert client.receivers() == []
    assert client.alerts() == []
    assert client.firing_alertnames() == {}
