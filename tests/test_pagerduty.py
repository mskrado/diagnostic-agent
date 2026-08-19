"""PagerDuty client + delivery helpers."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from app.clients.pagerduty import PagerDutyClient
from app.delivery.pagerduty import deliver_pagerduty


def _report(route: str = "escalate", confidence_note: str = "high") -> dict:
    return {
        "service": "platform-service",
        "alert_type": "HighErrorRate",
        "severity": "critical" if route == "escalate" else "warning",
        "route_decision": route,
        "blast_radius": ["postgres"],
        "diagnosis": {
            "primary_hypothesis": {
                "cause": "db pool saturation tenant-smoke-test",
                "confidence": 80,
            },
            "suggested_next_steps": ["Check pg_stat_activity"],
            "confidence_note": confidence_note,
        },
    }


def test_pagerduty_client_skips_when_unconfigured():
    client = PagerDutyClient("https://api.pagerduty.com", "", "", "")
    assert client.enabled is False
    assert client.create_incident(title="x", service="svc", severity="critical", details="d") is None
    assert client.add_note("P123", "note") is False


def test_pagerduty_client_creates_incident():
    client = PagerDutyClient(
        "https://api.pagerduty.com",
        "token",
        "service_id",
        "diag@example.com",
    )
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"incident": {"id": "P123"}}
    mock_http = MagicMock()
    mock_http.__enter__ = MagicMock(return_value=mock_http)
    mock_http.__exit__ = MagicMock(return_value=False)
    mock_http.post.return_value = response

    with patch("app.clients.pagerduty.httpx.Client", return_value=mock_http):
        incident_id = client.create_incident(
            title="HighErrorRate on platform-service",
            service="platform-service",
            severity="critical",
            details="details",
        )

    assert incident_id == "P123"
    payload = mock_http.post.call_args.kwargs["json"]
    assert payload["incident"]["title"].startswith("HighErrorRate")
    assert payload["incident"]["service"]["id"] == "service_id"


def test_pagerduty_client_adds_note():
    client = PagerDutyClient(
        "https://api.pagerduty.com",
        "token",
        "service_id",
        "diag@example.com",
    )
    response = MagicMock()
    response.raise_for_status = MagicMock()
    mock_http = MagicMock()
    mock_http.__enter__ = MagicMock(return_value=mock_http)
    mock_http.__exit__ = MagicMock(return_value=False)
    mock_http.post.return_value = response

    with patch("app.clients.pagerduty.httpx.Client", return_value=mock_http):
        assert client.add_note("P123", "hello") is True

    assert "/incidents/P123/notes" in mock_http.post.call_args.args[0]
    assert mock_http.post.call_args.kwargs["json"]["note"]["content"] == "hello"


def test_pagerduty_client_degrades_on_http_error():
    client = PagerDutyClient(
        "https://api.pagerduty.com",
        "token",
        "service_id",
        "diag@example.com",
    )
    mock_http = MagicMock()
    mock_http.__enter__ = MagicMock(return_value=mock_http)
    mock_http.__exit__ = MagicMock(return_value=False)
    mock_http.post.side_effect = httpx.HTTPError("boom")
    with patch("app.clients.pagerduty.httpx.Client", return_value=mock_http):
        assert client.create_incident(title="x", service="svc", severity="critical", details="d") is None
        assert client.add_note("P123", "hello") is False


def test_deliver_pagerduty_triggers_on_escalate():
    pagerduty = MagicMock()
    pagerduty.create_incident.return_value = "P123"
    with patch("app.delivery.pagerduty.settings") as mock_settings:
        mock_settings.pagerduty_enabled = True
        result = deliver_pagerduty(pagerduty, _report("escalate"))
    assert result == {"incident_id": "P123", "action": "triggered"}
    pagerduty.create_incident.assert_called_once()


def test_deliver_pagerduty_adds_note_on_existing_incident():
    pagerduty = MagicMock()
    pagerduty.add_note.return_value = True
    alert = {"annotations": {"pagerduty_incident_id": "P999", "summary": "5xx spike"}}
    with patch("app.delivery.pagerduty.settings") as mock_settings:
        mock_settings.pagerduty_enabled = True
        result = deliver_pagerduty(pagerduty, _report("report", confidence_note="high"), alert)
    assert result == {"incident_id": "P999", "action": "noted"}
    pagerduty.add_note.assert_called_once()


def test_deliver_pagerduty_skips_without_high_confidence_note():
    pagerduty = MagicMock()
    alert = {"annotations": {"pagerduty_incident_id": "P999"}}
    with patch("app.delivery.pagerduty.settings") as mock_settings:
        mock_settings.pagerduty_enabled = True
        result = deliver_pagerduty(pagerduty, _report("report", confidence_note="medium"), alert)
    assert result is None
    pagerduty.add_note.assert_not_called()
