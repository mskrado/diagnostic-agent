"""Read-only Alertmanager v2 API client.

Used by ``diag scan`` to learn which alerts a stack actually fires and where it
routes them. The agent itself never calls Alertmanager during a diagnosis — it
only receives its webhook.

``/api/v2/status`` also returns ``config.original``, the full Alertmanager
configuration, which routinely embeds Slack/PagerDuty webhook URLs and tokens.
This client deliberately never returns it; receiver *names* come from
``/api/v2/receivers`` instead, which carries no credentials.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class AlertmanagerClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            with httpx.Client(timeout=self._timeout) as client:
                r = client.get(f"{self.base_url}{path}", params=params)
                r.raise_for_status()
                return r.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Alertmanager GET %s failed: %s", path, exc)
            return None

    def version(self) -> str:
        """Alertmanager version, or an empty string when unavailable."""
        data = self._get("/api/v2/status")
        if not isinstance(data, dict):
            return ""
        info = data.get("versionInfo")
        if isinstance(info, dict):
            return str(info.get("version") or "")
        return ""

    def receivers(self) -> list[str]:
        """Configured receiver names (no credentials)."""
        data = self._get("/api/v2/receivers")
        if not isinstance(data, list):
            return []
        names: list[str] = []
        for entry in data:
            if isinstance(entry, dict) and entry.get("name"):
                names.append(str(entry["name"]))
        return names

    def alerts(self, *, active: bool = True, silenced: bool = False) -> list[dict]:
        """Alerts Alertmanager currently holds, newest state as it sees it."""
        params = {
            "active": str(active).lower(),
            "silenced": str(silenced).lower(),
            "inhibited": "false",
        }
        data = self._get("/api/v2/alerts", params=params)
        return [a for a in data if isinstance(a, dict)] if isinstance(data, list) else []

    def firing_alertnames(self) -> dict[str, int]:
        """``alertname`` -> count among currently held alerts.

        What actually pages a host is better prioritisation than what its rules
        merely define.
        """
        counts: dict[str, int] = {}
        for alert in self.alerts():
            labels = alert.get("labels")
            if not isinstance(labels, dict):
                continue
            name = str(labels.get("alertname") or "").strip()
            if name:
                counts[name] = counts.get(name, 0) + 1
        return counts
