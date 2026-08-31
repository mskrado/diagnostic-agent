"""Read-only Grafana Loki HTTP API client.

Queries assume the shipper (Promtail/Alloy) promotes low-cardinality fields such
as `service` and `level` to Loki labels and keeps the full structured JSON line
as the log message, e.g.:  {service="platform-service"} | json | level=~"ERROR|WARN"
The host's own label plane is declared in the workspace profile, not here.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

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
        # Loki returns streams in arbitrary order; callers take [:N] as the
        # "newest" sample, so sort globally by timestamp (newest first).
        entries.sort(key=lambda pair: int(pair[0]), reverse=True)
        return entries

    def query_range_streams(
        self, logql: str, lookback_minutes: int = 15, limit: int = 100
    ) -> list[tuple[dict[str, str], list[str]]]:
        """Like :meth:`query_range` but keeps each stream's labels.

        ``query_range`` flattens streams into one time-ordered list, which is
        what a diagnosis wants. Discovery needs the opposite: *which* stream a
        line came from is the answer (e.g. finding the stream that carries a
        dependency's errors when it has no stream of its own).
        """
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=lookback_minutes)
        params = {
            "query": logql,
            "start": int(start.timestamp() * 1e9),
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

        out: list[tuple[dict[str, str], list[str]]] = []
        for stream in streams:
            labels = stream.get("stream")
            if not isinstance(labels, dict):
                continue
            lines = [line for _ts, line in stream.get("values", [])]
            out.append(({str(k): str(v) for k, v in labels.items()}, lines))
        return out

    # -- discovery -----------------------------------------------------------
    def _get_data(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET an API path and return its ``data`` payload, or None on failure."""
        try:
            with httpx.Client(timeout=self._timeout) as client:
                r = client.get(f"{self.base_url}{path}", params=params)
                r.raise_for_status()
                return r.json().get("data")
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Loki GET %s failed: %s", path, exc)
            return None

    def labels(self) -> list[str]:
        """Every label name Loki currently indexes."""
        data = self._get_data("/loki/api/v1/labels")
        return [str(name) for name in data] if isinstance(data, list) else []

    def label_values(self, label: str) -> list[str]:
        """Values for one label, e.g. the ``service`` values that have streams."""
        data = self._get_data(f"/loki/api/v1/label/{label}/values")
        return [str(value) for value in data] if isinstance(data, list) else []

    def series(self, match: list[str]) -> list[dict]:
        """Stream label sets matching one or more selectors."""
        if not match:
            return []
        data = self._get_data("/loki/api/v1/series", params={"match[]": match})
        return [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []

    def rules(self) -> dict[str, list[dict]]:
        """Ruler rule groups, keyed by namespace.

        Unlike every other Loki endpoint this one answers **YAML**, and it 404s
        when the ruler is disabled — a common, unremarkable configuration, so
        callers get an empty mapping rather than an error.
        """
        import yaml

        try:
            with httpx.Client(timeout=self._timeout) as client:
                r = client.get(f"{self.base_url}/loki/api/v1/rules")
                if r.status_code == 404:
                    logger.info("Loki ruler is not enabled (404 on /rules)")
                    return {}
                r.raise_for_status()
                data = yaml.safe_load(r.text) or {}
        except (httpx.HTTPError, yaml.YAMLError) as exc:
            logger.warning("Loki rules fetch failed: %s", exc)
            return {}

        if not isinstance(data, dict):
            return {}
        return {
            str(namespace): [g for g in groups if isinstance(g, dict)]
            for namespace, groups in data.items()
            if isinstance(groups, list)
        }

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
