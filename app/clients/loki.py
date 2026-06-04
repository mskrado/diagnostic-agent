"""Read-only Grafana Loki HTTP API client.

publishi.ai's Promtail pipeline promotes `service`, `level` and `tenantId` to
Loki labels and keeps the full Spring Boot JSON line as the log message. So a
typical query is:  {service="platform-service"} | json | level="ERROR"
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)


class LokiClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout

    def query_range(
        self, logql: str, lookback_minutes: int = 15, limit: int = 500
    ) -> list[str]:
        """Return raw log lines (newest first) for a LogQL query."""
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=lookback_minutes)
        params = {
            "query": logql,
            "start": int(start.timestamp() * 1e9),  # nanoseconds
            "end": int(end.timestamp() * 1e9),
            "limit": limit,
            "direction": "backward",
        }
        try:
            with httpx.Client(timeout=self._timeout) as client:
                r = client.get(
                    f"{self.base_url}/loki/api/v1/query_range", params=params
                )
                r.raise_for_status()
                streams = r.json().get("data", {}).get("result", [])
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Loki query failed (%s): %s", logql, exc)
            return []

        lines: list[str] = []
        for stream in streams:
            for _ts, line in stream.get("values", []):
                lines.append(line)
        return lines

    @staticmethod
    def extract_messages(lines: list[str]) -> list[str]:
        """Pull the human-readable `message` out of Spring Boot JSON log lines.

        Falls back to the raw line for non-JSON entries.
        """
        out: list[str] = []
        for line in lines:
            try:
                doc = json.loads(line)
                msg = doc.get("message") or doc.get("msg") or line
                logger_name = doc.get("logger_name", "")
                short = logger_name.split(".")[-1] if logger_name else ""
                out.append(f"{short}: {msg}" if short else msg)
            except (ValueError, AttributeError):
                out.append(line)
        return out
