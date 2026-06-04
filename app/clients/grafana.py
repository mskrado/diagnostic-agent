"""Grafana HTTP API client.

Used for two things:
  1. (optional) pulling current alert state for extra context
  2. writing an annotation back to Grafana when a diagnostic completes, so the
     report is visible on dashboards at the moment of the incident.

A Viewer-role service-account token is enough to read; annotation writes need
the `annotations:write` permission. All calls degrade gracefully if the token
is missing or the call fails -- the agent never blocks on Grafana.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class GrafanaClient:
    def __init__(self, base_url: str, token: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self._token)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def post_annotation(
        self, text: str, tags: list[str], time_ms: int | None = None
    ) -> bool:
        """Create a Grafana annotation. Returns True on success."""
        if not self.enabled:
            logger.info("Grafana token not set; skipping annotation")
            return False
        payload: dict = {"text": text, "tags": tags}
        if time_ms is not None:
            payload["time"] = time_ms
        try:
            with httpx.Client(timeout=self._timeout) as client:
                r = client.post(
                    f"{self.base_url}/api/annotations",
                    headers=self._headers(),
                    json=payload,
                )
                r.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            logger.warning("Grafana annotation failed: %s", exc)
            return False
