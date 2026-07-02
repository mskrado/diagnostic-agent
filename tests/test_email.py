"""SMTP diagnostic email delivery."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.delivery.email import deliver_email, format_diagnosis_email


def test_format_diagnosis_email_includes_hypothesis():
    report = {
        "service": "platform-service",
        "alert_type": "HighErrorRate",
        "severity": "warning",
        "blast_radius": ["postgres"],
        "diagnosis": {
            "primary_hypothesis": {
                "cause": "db pool saturation",
                "confidence": 80,
                "evidence": "hikaricp_connections_pending > 0",
            },
            "secondary_hypotheses": [{"cause": "redis timeout", "confidence": 40}],
            "suggested_next_steps": ["Check pg_stat_activity"],
        },
        "evidence": {"rag_used": True, "metrics": {"platform-service": {"up": 1.0}}},
    }
    alert = {
        "annotations": {"summary": "5xx spike on platform-service"},
        "labels": {"alertname": "HighErrorRate"},
    }
    subject, plain, html = format_diagnosis_email(report, alert)
    assert "diagnostic" in subject
    assert "HighErrorRate" in subject
    assert "db pool saturation" in plain
    assert "5xx spike" in plain
    assert "redis timeout" in plain
    assert "pg_stat_activity" in plain
    assert "db pool saturation" in html


def test_format_diagnosis_email_llm_error():
    report = {
        "service": "platform-service",
        "alert_type": "HighErrorRate",
        "severity": "warning",
        "blast_radius": [],
        "diagnosis": {"error": "LLM call failed: 401"},
        "evidence": {"rag_used": False, "metrics": {}},
    }
    _, plain, _ = format_diagnosis_email(report)
    assert "Diagnosis unavailable" in plain
    assert "401" in plain


def test_deliver_email_skips_when_disabled():
    with patch("app.delivery.email.settings") as mock_settings:
        mock_settings.email_enabled = False
        assert deliver_email({"service": "x"}) is False


def test_deliver_email_skips_without_recipients():
    with patch("app.delivery.email.settings") as mock_settings:
        mock_settings.email_enabled = True
        mock_settings.email_to = ""
        assert deliver_email({"service": "x"}) is False


def test_deliver_email_sends_via_smtp():
    report = {
        "service": "platform-service",
        "alert_type": "HighErrorRate",
        "severity": "warning",
        "blast_radius": [],
        "diagnosis": {
            "primary_hypothesis": {"cause": "test cause", "confidence": 50},
        },
        "evidence": {"rag_used": False, "metrics": {}},
    }
    mock_smtp = MagicMock()
    mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
    mock_smtp.__exit__ = MagicMock(return_value=False)

    with patch("app.delivery.email.settings") as mock_settings, patch(
        "app.delivery.email.smtplib.SMTP", return_value=mock_smtp
    ):
        mock_settings.email_enabled = True
        mock_settings.email_to = "dev-alerts@localhost"
        mock_settings.email_subject_prefix = "publishi"
        mock_settings.smtp_host = "mailpit"
        mock_settings.smtp_port = 1025
        mock_settings.smtp_from = "diagnostic-agent@publishi.local"
        mock_settings.smtp_username = ""
        mock_settings.smtp_password = ""
        mock_settings.smtp_starttls = False
        mock_settings.smtp_timeout = 15.0

        assert deliver_email(report) is True

    mock_smtp.sendmail.assert_called_once()
    recipients = mock_smtp.sendmail.call_args[0][1]
    assert recipients == ["dev-alerts@localhost"]
