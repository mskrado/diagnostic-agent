"""Loads and queries the service dependency map (service_map.yaml)."""
from __future__ import annotations

import functools
import logging

import yaml

logger = logging.getLogger(__name__)


class DependencyMap:
    def __init__(self, data: dict):
        self._services: dict = data.get("services", {})
        self._module_deps: dict = data.get("module_dependencies", {})

    @classmethod
    def load(cls, path: str) -> "DependencyMap":
        if not path:
            logger.info(
                "No service_map.yaml in the active profile; running with an empty "
                "dependency map (blast radius will be empty)"
            )
            return cls({})
        try:
            with open(path, encoding="utf-8") as f:
                return cls(yaml.safe_load(f) or {})
        except (OSError, yaml.YAMLError) as exc:
            logger.error("Failed to load dependency map %s: %s", path, exc)
            return cls({})

    def known_services(self) -> list[str]:
        return list(self._services.keys())

    def info(self, service: str) -> dict:
        return self._services.get(service, {})

    def kind(self, service: str) -> str:
        return self._services.get(service, {}).get("kind", "unknown")

    def upstream(self, service: str) -> list[str]:
        return list(self._services.get(service, {}).get("upstream", []))

    def downstream(self, service: str) -> list[str]:
        return list(self._services.get(service, {}).get("downstream", []))

    def neighbours(self, service: str) -> list[str]:
        """Upstream + downstream, de-duplicated, preserving order."""
        seen: dict[str, None] = {}
        for s in self.upstream(service) + self.downstream(service):
            seen.setdefault(s, None)
        return list(seen.keys())

    def blast_radius(self, service: str) -> list[str]:
        """Services likely degraded if `service` fails (its downstream)."""
        return self.downstream(service)

    def module_dependencies(self, module: str) -> list[str]:
        return list(self._module_deps.get(module, []))

    def log_services(self, service: str) -> list[str]:
        """Loki ``service=`` labels that hold lines for this alert target.

        Backing deps and logical targets (postgres, security, …) emit through
        platform-service / api-gateway; defaults to ``[service]`` when unset.
        """
        explicit = self._services.get(service, {}).get("log_services")
        if explicit:
            return list(explicit)
        return [service] if service else []

    def log_selector(self, service: str) -> str | None:
        """Optional full Loki stream selector override (e.g. Faro ``{app=}``)."""
        sel = self._services.get(service, {}).get("log_selector")
        return str(sel).strip() if sel else None

    def resolve(self, raw: str) -> str:
        """Best-effort map an alert label (job/service) to a known service.

        Handles e.g. "platform-service:8080" or "c.p.auth" style hints.
        """
        if raw in self._services:
            return raw
        candidate = raw.split(":")[0].strip()
        if candidate in self._services:
            return candidate
        # module hint like c.p.auth -> auth (not a service itself, but useful)
        return candidate or raw


@functools.lru_cache(maxsize=1)
def get_dependency_map(path: str) -> DependencyMap:
    return DependencyMap.load(path)
