"""PagerDuty REST API client (incident create + notes).

Outbound-first integration for the diagnostic agent:
1. create a PagerDuty incident when the routed diagnosis escalates
2. add a note to an existing incident when a high-confidence diagnosis arrives

All calls degrade gracefully; delivery must never block diagnosis completion.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class PagerDutyClient:
    def __init__(
        self,
        base_url: str,
        api_token: str,
        service_id: str,
        from_email: str,
        timeout: float = 10.0,
    ):
        self.base_url = base_url.rstrip("/")
        self._token = api_token
        self._service_id = service_id
        self._from_email = from_email
        self._timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._from_email)

    @property
    def can_trigger(self) -> bool:
        return bool(self.enabled and self._service_id)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Token token={self._token}",
            "Accept": "application/vnd.pagerduty+json;version=2",
            "Content-Type": "application/json",
            "From": self._from_email,
        }

    def create_incident(
        self, *, title: str, service: str, severity: str, details: str
    ) -> str | None:
        if not self.can_trigger:
            logger.info("PagerDuty trigger skipped (token/service/from not configured)")
            return None
        urgency = "high" if (severity or "").lower() == "critical" else "low"
        payload = {
            "incident": {
                "type": "incident",
                "title": title,
                "service": {"id": self._service_id, "type": "service_reference"},
                "urgency": urgency,
                "body": {"type": "incident_body", "details": details},
            }
        }
        try:
            with httpx.Client(timeout=self._timeout) as client:
                r = client.post(
                    f"{self.base_url}/incidents",
                    headers=self._headers(),
                    json=payload,
                )
                r.raise_for_status()
            body = r.json() or {}
            incident = body.get("incident") or {}
            return incident.get("id")
        except httpx.HTTPError as exc:
            logger.warning("PagerDuty incident create failed: %s", exc)
            return None

    def add_note(self, incident_id: str, note: str) -> bool:
        if not self.enabled:
            logger.info("PagerDuty note skipped (token/from not configured)")
            return False
        try:
            with httpx.Client(timeout=self._timeout) as client:
                r = client.post(
                    f"{self.base_url}/incidents/{incident_id}/notes",
                    headers=self._headers(),
                    json={"note": {"content": note}},
                )
                r.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            logger.warning("PagerDuty note failed: %s", exc)
            return False
