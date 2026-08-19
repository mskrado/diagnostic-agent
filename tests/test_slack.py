"""Slack webhook delivery for diagnostic traces."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.delivery.slack import deliver_slack, format_diagnosis_slack


def _report() -> dict:
    return {
        "service": "platform-service",
        "alert_type": "HighErrorRate",
        "severity": "warning",
        "severity_normalized": "SEV3",
        "route_decision": "execute",
        "blast_radius": ["postgres"],
        "diagnosis": {
            "primary_hypothesis": {
                "cause": "db pool saturation tenant-smoke-test",
                "confidence": 80,
            },
            "suggested_next_steps": ["Check pg_stat_activity"],
            "confidence_note": "high",
        },
    }


def test_format_diagnosis_slack_includes_route_and_redacts():
    payload = format_diagnosis_slack(
        _report(),
        {"annotations": {"summary": "5xx spike for tenant-smoke-test"}},
    )
    assert "route=execute" in payload["text"]
    assert "tenant-smoke-test" not in payload["text"]
    assert "tenant-[REDACTED]" in payload["text"]
    joined = str(payload["blocks"])
    assert "HighErrorRate on platform-service" in joined
    assert "Primary hypothesis" in joined
    assert "Check pg_stat_activity" in joined


def test_deliver_slack_skips_when_disabled():
    with patch("app.delivery.slack.settings") as mock_settings:
        mock_settings.slack_enabled = False
        assert deliver_slack(_report()) is False


def test_deliver_slack_skips_without_webhook():
    with patch("app.delivery.slack.settings") as mock_settings:
        mock_settings.slack_enabled = True
        mock_settings.slack_webhook_url = ""
        assert deliver_slack(_report()) is False


def test_deliver_slack_posts_webhook():
    response = MagicMock()
    response.raise_for_status.return_value = None
    with patch("app.delivery.slack.settings") as mock_settings, patch(
        "app.delivery.slack.httpx.post", return_value=response
    ) as mock_post:
        mock_settings.slack_enabled = True
        mock_settings.slack_webhook_url = "https://hooks.slack.test/123"
        mock_settings.slack_channel = "#incidents"
        mock_settings.slack_username = "diagnostic-agent"
        mock_settings.slack_timeout = 5.0
        assert deliver_slack(_report()) is True

    mock_post.assert_called_once()
    kwargs = mock_post.call_args.kwargs
    assert kwargs["timeout"] == 5.0
    assert kwargs["json"]["channel"] == "#incidents"
    assert kwargs["json"]["text"].startswith("HighErrorRate on platform-service")
