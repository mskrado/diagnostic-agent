"""Thin read-only Prometheus HTTP API client."""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class PrometheusClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout

    def instant(self, promql: str) -> float | None:
        """Run an instant query and return the first scalar value, or None."""
        try:
            with httpx.Client(timeout=self._timeout) as client:
                r = client.get(
                    f"{self.base_url}/api/v1/query", params={"query": promql}
                )
                r.raise_for_status()
                result = r.json().get("data", {}).get("result", [])
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Prometheus query failed (%s): %s", promql, exc)
            return None

        if not result:
            return None
        try:
            return float(result[0]["value"][1])
        except (KeyError, IndexError, ValueError):
            return None

    def instant_raw(self, promql: str) -> list[dict]:
        """Run an instant query and return the raw result vector."""
        try:
            with httpx.Client(timeout=self._timeout) as client:
                r = client.get(
                    f"{self.base_url}/api/v1/query", params={"query": promql}
                )
                r.raise_for_status()
                return r.json().get("data", {}).get("result", [])
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Prometheus query failed (%s): %s", promql, exc)
            return []
