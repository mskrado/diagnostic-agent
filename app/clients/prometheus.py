"""Thin read-only Prometheus HTTP API client.

Two groups of methods:

* ``instant`` / ``instant_raw`` — evaluate PromQL during a diagnosis.
* the discovery helpers (``metric_names``, ``targets``, ``rules``, …) — describe
  what a stack exposes, so ``diag scan`` can report what the agent can see
  without anyone hand-writing a workspace first.

Every method degrades to an empty result and a logged warning: a scan of a
partially reachable stack is still useful.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class PrometheusClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout

    # -- query ---------------------------------------------------------------
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

    # -- discovery -----------------------------------------------------------
    def _get_data(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET an API path and return its ``data`` payload, or None on failure."""
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=self._timeout) as client:
                r = client.get(url, params=params)
                r.raise_for_status()
                return r.json().get("data")
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Prometheus GET %s failed: %s", path, exc)
            return None

    def build_info(self) -> dict:
        """Version / build metadata, or an empty dict when unavailable."""
        data = self._get_data("/api/v1/status/buildinfo")
        return data if isinstance(data, dict) else {}

    def labels(self) -> list[str]:
        """Every label name known to Prometheus."""
        data = self._get_data("/api/v1/labels")
        return [str(name) for name in data] if isinstance(data, list) else []

    def label_values(self, label: str) -> list[str]:
        """Values for one label, e.g. ``service`` or ``job``."""
        data = self._get_data(f"/api/v1/label/{label}/values")
        return [str(value) for value in data] if isinstance(data, list) else []

    def metric_names(self) -> list[str]:
        """Every metric name (the ``__name__`` label's values)."""
        return self.label_values("__name__")

    def metadata(self) -> dict[str, list[dict]]:
        """Metric name -> metadata entries (type, help, unit)."""
        data = self._get_data("/api/v1/metadata")
        if not isinstance(data, dict):
            return {}
        return {
            str(name): [e for e in entries if isinstance(e, dict)]
            for name, entries in data.items()
            if isinstance(entries, list)
        }

    def series(self, match: list[str]) -> list[dict]:
        """Label sets matching one or more selectors."""
        if not match:
            return []
        data = self._get_data("/api/v1/series", params={"match[]": match})
        return [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []

    def targets(self, state: str = "active") -> list[dict]:
        """Scrape targets. ``state`` is ``active``, ``dropped``, or ``any``."""
        data = self._get_data("/api/v1/targets", params={"state": state})
        if not isinstance(data, dict):
            return []
        key = "droppedTargets" if state == "dropped" else "activeTargets"
        targets = data.get(key) or []
        return [t for t in targets if isinstance(t, dict)]

    def rules(self) -> list[dict]:
        """Rule groups (``/api/v1/rules``), each carrying its own ``rules`` list."""
        data = self._get_data("/api/v1/rules")
        if not isinstance(data, dict):
            return []
        groups = data.get("groups") or []
        return [g for g in groups if isinstance(g, dict)]
