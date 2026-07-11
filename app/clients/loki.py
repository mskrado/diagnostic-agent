"""Read-only Grafana Loki HTTP API client.

publishi.ai's Promtail pipeline promotes `service`, `level` and `tenantId` to
Loki labels and keeps the full Spring Boot JSON line as the log message. So a
typical query is:  {service="platform-service"} | json | level=~"ERROR|WARN"
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
    ) -> list[tuple[str, str]]:
        """Return (loki_ts_ns, raw_line) pairs, newest first, for a LogQL query."""
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

        entries: list[tuple[str, str]] = []
        for stream in streams:
            for ts_ns, line in stream.get("values", []):
                entries.append((ts_ns, line))
        return entries

    @staticmethod
    def format_log_entries(entries: list[tuple[str, str]]) -> list[str]:
        """Format log lines for reports/email with timestamp and trace_id."""
        return [LokiClient._format_log_entry(ts_ns, line) for ts_ns, line in entries]

    @staticmethod
    def _format_log_entry(ts_ns: str, line: str) -> str:
        """Pull message, @timestamp, and trace_id from Spring Boot JSON log lines."""
        timestamp = LokiClient._loki_ts_to_iso(ts_ns)
        trace_id = "n/a"
        body = line
        try:
            doc = json.loads(line)
            timestamp = doc.get("@timestamp") or timestamp
            trace_id = doc.get("trace_id") or doc.get("traceId") or trace_id
            msg = doc.get("message") or doc.get("msg") or line
            logger_name = doc.get("logger_name", "")
            short = logger_name.split(".")[-1] if logger_name else ""
            body = f"{short}: {msg}" if short else msg
        except (ValueError, AttributeError, TypeError):
            pass
        return f"[{timestamp}] [trace_id={trace_id}] {body}"

    @staticmethod
    def _loki_ts_to_iso(ts_ns: str) -> str:
        try:
            sec = int(ts_ns) / 1e9
            return (
                datetime.fromtimestamp(sec, tz=timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )
        except (ValueError, TypeError, OSError):
            return "unknown"
