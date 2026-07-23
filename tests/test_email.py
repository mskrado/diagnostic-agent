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
        "models": {
            "chat_provider": "bedrock",
            "chat_model": "amazon.nova-micro-v1:0",
            "embed_provider": "bedrock",
            "embed_model": "amazon.titan-embed-text-v2:0",
        },
        "diagnosis": {
            "primary_hypothesis": {
                "cause": "db pool saturation",
                "confidence": 80,
                "evidence": "hikaricp_connections_pending > 0",
            },
            "secondary_hypotheses": [{"cause": "redis timeout", "confidence": 40}],
            "suggested_next_steps": ["Check pg_stat_activity"],
            "confidence_note": "high",
        },
        "evidence": {
            "rag_used": True,
            "metrics": {"platform-service": {"up": 1.0}},
            "log_source": {
                "system": "loki",
                "url": "http://loki:3100",
                "logql": '{service="platform-service"} | json | level=~"ERROR|WARN"',
                "lookback_minutes": 15,
                "level": "ERROR|WARN",
                "service": "platform-service",
            },
            "error_log_sample": [
                "HikariPool: Connection is not available, tenant-smoke-test",
                "JdbcTemplate: query failed for user 550e8400-e29b-41d4-a716-446655440000",
                "[2026-07-07T06:58:12.345Z] [trace_id=abc123] "
                "OpenAIHealthIndicator: OpenAI health check failed: 401 Unauthorized",
            ],
        },
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
    assert "Models:" in plain
    assert "Diagnosis: bedrock / amazon.nova-micro-v1:0" in plain
    assert "Embeddings: bedrock / amazon.titan-embed-text-v2:0" in plain
    assert "Confidence note: high" in plain
    assert "Models" in html
    assert "amazon.nova-micro-v1:0" in html
    assert "Log source:" in plain
    assert "http://loki:3100" in plain
    assert 'service="platform-service"' in plain
    assert "Lookback: 15m" in plain
    assert "Recent error/warn logs:" in plain
    assert "Connection is not available" in plain
    assert "tenant-smoke-test" not in plain
    assert "tenant-[REDACTED]" in plain
    assert "550e8400-e29b-41d4-a716-446655440000" not in plain
    assert "[UUID-REDACTED]" in plain
    assert "[trace_id=abc123]" in plain
    assert "Log source" in html
    assert "Recent error/warn logs" in html
    assert 'level=~"ERROR|WARN"' in plain
    assert "http://loki:3100" in html
    assert "OpenAIHealthIndicator" in html


def test_format_diagnosis_email_renders_issue_categories():
    report = {
        "service": "platform-service",
        "alert_type": "HighErrorRate",
        "severity": "critical",
        "blast_radius": ["postgres", "redis"],
        "diagnosis": {
            "issue_categories": [
                {
                    "category": "database",
                    "cause": "Postgres connection refused",
                    "confidence": 85,
                    "evidence": "Connection to postgres:5432 refused",
                    "suggested_next_step": "Check postgres container health",
                },
                {
                    "category": "cache",
                    "cause": "Redis command timeout",
                    "confidence": 60,
                    "evidence": "Command timed out after 5 second(s)",
                    "suggested_next_step": "Check redis latency",
                },
            ],
            "primary_hypothesis": {
                "cause": "Postgres connection refused",
                "confidence": 85,
                "evidence": "Connection to postgres:5432 refused",
            },
            "secondary_hypotheses": [{"cause": "Redis command timeout", "confidence": 60}],
            "suggested_next_steps": ["Check postgres", "Check redis"],
        },
        "evidence": {"rag_used": False, "metrics": {}, "error_log_sample": []},
    }
    _, plain, html = format_diagnosis_email(report)
    assert "Issue categories" in plain
    assert "[database] Postgres connection refused (85%)" in plain
    assert "evidence: Connection to postgres:5432 refused" in plain
    assert "[cache] Redis command timeout (60%)" in plain
    assert "next: Check postgres container health" in plain
    assert "Issue categories" in html
    assert "Postgres connection refused" in html
    assert "Connection to postgres:5432 refused" in html


def test_format_diagnosis_email_renders_tools_and_fixes():
    report = {
        "service": "platform-service",
        "alert_type": "PostgresErrorsInLogs",
        "severity": "warning",
        "blast_radius": ["postgres"],
        "diagnosis": {
            "issue_categories": [
                {
                    "category": "database",
                    "cause": "Postgres connection refused",
                    "confidence": 90,
                    "evidence": "Connection refused",
                    "suggested_next_step": "Check postgres container",
                    "tool_run_examples": ["docker compose ps postgres"],
                    "fix_suggestions": ["Restart postgres if exited (brief downtime)."],
                }
            ],
            "primary_hypothesis": {
                "cause": "Postgres connection refused",
                "confidence": 90,
                "evidence": "Connection refused",
            },
            "suggested_next_steps": ["Check postgres container"],
            "tool_run_examples": [
                "docker logs publishi-postgres --tail 100",
                "curl -sf http://localhost:8080/actuator/health",
            ],
            "fix_suggestions": [
                "Verify SPRING_DATASOURCE_PASSWORD matches POSTGRES_PASSWORD.",
                "Restart postgres if the container is exited (brief downtime).",
            ],
            "confidence_note": "high",
        },
        "evidence": {"rag_used": False, "metrics": {}, "error_log_sample": []},
    }
    _, plain, html = format_diagnosis_email(report)
    # With issue_categories present, tools/fixes live inside each category card
    # and the redundant global sections are dropped.
    assert "tool runs:" in plain
    assert "$ docker compose ps postgres" in plain
    assert "fixes:" in plain
    assert "Restart postgres if exited (brief downtime)." in plain
    assert "docker compose ps postgres" in html
    assert "Restart postgres if exited (brief downtime)." in html
    assert "no auto-remediation" in plain


def test_format_diagnosis_email_includes_judge_when_present():
    report = {
        "service": "platform-service",
        "alert_type": "HighErrorRate",
        "severity": "warning",
        "blast_radius": [],
        "models": {
            "chat_provider": "bedrock",
            "chat_model": "amazon.nova-lite-v1:0",
            "embed_provider": "bedrock",
            "embed_model": "amazon.titan-embed-text-v2:0",
        },
        "judge": {
            "score": 4,
            "correct": True,
            "reason": "Found postgres and redis in issue_categories",
            "models": {
                "chat_provider": "bedrock",
                "chat_model": "amazon.nova-micro-v1:0",
            },
        },
        "diagnosis": {
            "primary_hypothesis": {"cause": "multi-failure", "confidence": 70},
        },
        "evidence": {"rag_used": False, "metrics": {}},
    }
    _, plain, html = format_diagnosis_email(report)
    assert "Judge:" in plain
    assert "Score: 4/5" in plain
    assert "Correct: yes" in plain
    assert "Found postgres and redis in issue_categories" in plain
    assert "Judge: bedrock / amazon.nova-micro-v1:0" in plain
    assert "Judge" in html
    assert "4/5" in html
    assert "Found postgres and redis" in html


def test_format_diagnosis_email_omits_judge_when_absent():
    report = {
        "service": "platform-service",
        "alert_type": "HighErrorRate",
        "severity": "warning",
        "blast_radius": [],
        "diagnosis": {
            "primary_hypothesis": {"cause": "x", "confidence": 50},
        },
        "evidence": {"rag_used": False, "metrics": {}},
    }
    _, plain, html = format_diagnosis_email(report)
    assert "Judge:" not in plain
    assert "<h3>Judge</h3>" not in html
    assert "Models:" in plain  # falls back to settings snapshot


def test_format_diagnosis_email_empty_logs():
    report = {
        "service": "platform-service",
        "alert_type": "HighErrorRate",
        "severity": "warning",
        "blast_radius": [],
        "diagnosis": {},
        "evidence": {"rag_used": False, "metrics": {}, "error_log_sample": []},
    }
    _, plain, _ = format_diagnosis_email(report)
    assert "Log source:" in plain
    assert "source not recorded" in plain
    assert "Recent error/warn logs:" in plain
    assert "none" in plain


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
