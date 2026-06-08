"""Grafana client + annotation delivery (graceful degradation)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from app.clients.grafana import GrafanaClient
from app.delivery.annotation import deliver_annotation


def test_grafana_client_skips_when_token_empty():
    client = GrafanaClient("http://grafana:3000", "")
    assert client.enabled is False
    assert client.post_annotation("test", ["diagnostic-agent"]) is False


def test_grafana_client_posts_when_token_set():
    client = GrafanaClient("http://grafana:3000", "glsa_test")
    response = MagicMock()
    response.raise_for_status = MagicMock()
    mock_http = MagicMock()
    mock_http.__enter__ = MagicMock(return_value=mock_http)
    mock_http.__exit__ = MagicMock(return_value=False)
    mock_http.post.return_value = response

    with patch("app.clients.grafana.httpx.Client", return_value=mock_http):
        ok = client.post_annotation("hello", ["diagnostic-agent"], time_ms=123)

    assert ok is True
    mock_http.post.assert_called_once()
    call_kwargs = mock_http.post.call_args.kwargs
    assert call_kwargs["json"]["text"] == "hello"
    assert call_kwargs["json"]["time"] == 123
    assert call_kwargs["headers"]["Authorization"] == "Bearer glsa_test"


def test_grafana_client_degrades_on_http_error():
    client = GrafanaClient("http://grafana:3000", "glsa_test")
    mock_http = MagicMock()
    mock_http.__enter__ = MagicMock(return_value=mock_http)
    mock_http.__exit__ = MagicMock(return_value=False)
    mock_http.post.side_effect = httpx.HTTPError("connection refused")

    with patch("app.clients.grafana.httpx.Client", return_value=mock_http):
        assert client.post_annotation("hello", ["diagnostic-agent"]) is False


def test_deliver_annotation_skips_when_disabled():
    grafana = GrafanaClient("http://grafana:3000", "token")
    with patch("app.delivery.annotation.settings") as mock_settings:
        mock_settings.grafana_annotations_enabled = False
        assert deliver_annotation(grafana, {"service": "x"}) is False


def test_deliver_annotation_skips_without_token():
    grafana = GrafanaClient("http://grafana:3000", "")
    with patch("app.delivery.annotation.settings") as mock_settings:
        mock_settings.grafana_annotations_enabled = True
        assert deliver_annotation(
            grafana,
            {
                "service": "platform-service",
                "alert_type": "HighErrorRate",
                "diagnosis": {
                    "primary_hypothesis": {"cause": "db timeout", "confidence": 80}
                },
                "blast_radius": ["postgres"],
            },
        ) is False


def test_deliver_annotation_builds_summary():
    grafana = MagicMock()
    grafana.post_annotation.return_value = True
    report = {
        "service": "platform-service",
        "alert_type": "HighErrorRate",
        "diagnosis": {
            "primary_hypothesis": {"cause": "db timeout", "confidence": 80}
        },
        "blast_radius": ["postgres"],
    }
    with patch("app.delivery.annotation.settings") as mock_settings:
        mock_settings.grafana_annotations_enabled = True
        assert deliver_annotation(grafana, report) is True

    grafana.post_annotation.assert_called_once()
    call = grafana.post_annotation.call_args
    text = call.kwargs.get("text") or call.args[0]
    tags = call.kwargs.get("tags") or call.args[1]
    assert "HighErrorRate" in text
    assert "platform-service" in text
    assert "diagnostic-agent" in tags
