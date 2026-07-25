"""Typed views over integration-profile YAML documents."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MetricsProfile:
    """PromQL templates and which kinds receive which probes."""

    # Named templates; placeholders: {service}, {window}
    templates: dict[str, str] = field(default_factory=dict)
    # Kinds that receive the "service" metric suite (error_rate, latency, …)
    service_kinds: tuple[str, ...] = ("gateway", "monolith", "http", "unknown")
    # Metric names (keys in templates) collected for service_kinds
    service_metrics: tuple[str, ...] = (
        "error_rate",
        "request_rate",
        "latency_p99",
        "service_up",
        "jvm_heap_used_ratio",
    )
    # Always collected on the alerted service (if template exists)
    always_collect: tuple[str, ...] = ("db_pool_pending",)
    # kind -> template name or inline PromQL string
    dependency_probes: dict[str, str] = field(default_factory=dict)
    # Preset name this profile extends (informational / merge source)
    extends: str | None = None

    def render(self, name: str, *, service: str, window: str = "5m") -> str | None:
        tmpl = self.templates.get(name)
        if not tmpl:
            return None
        return tmpl.format(service=service, window=window)

    def probe_for_kind(self, kind: str, *, service: str, window: str = "5m") -> str | None:
        raw = self.dependency_probes.get(kind)
        if not raw:
            return None
        if raw in self.templates:
            return self.render(raw, service=service, window=window)
        return raw.format(service=service, window=window)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "MetricsProfile":
        data = data or {}
        templates = dict(data.get("templates") or {})
        # Allow shorthand top-level metric keys (legacy-friendly)
        for key, val in data.items():
            if key in (
                "extends",
                "templates",
                "service_kinds",
                "service_metrics",
                "always_collect",
                "dependency_probes",
            ):
                continue
            if isinstance(val, str):
                templates.setdefault(key, val)
        def _list(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
            # `key: []` means "none", which must not fall through to the default.
            value = data.get(key)
            return default if value is None else tuple(value)

        return cls(
            templates=templates,
            service_kinds=_list("service_kinds", cls.service_kinds),
            service_metrics=_list("service_metrics", cls.service_metrics),
            always_collect=_list("always_collect", cls.always_collect),
            dependency_probes=dict(data.get("dependency_probes") or {}),
            extends=data.get("extends"),
        )


@dataclass(frozen=True)
class LogsProfile:
    """Loki label convention, level filter, alert line filters, module regex."""

    service_label: str = "service"
    level_filter: str = "ERROR|WARN"
    use_json_parser: bool = True
    module_regex: str | None = None
    alert_line_filters: dict[str, str] = field(default_factory=dict)
    extends: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "LogsProfile":
        data = data or {}
        return cls(
            service_label=str(data.get("service_label") or cls.service_label),
            level_filter=str(data.get("level_filter") or cls.level_filter),
            use_json_parser=bool(data.get("use_json_parser", cls.use_json_parser)),
            module_regex=data.get("module_regex"),
            alert_line_filters=dict(data.get("alert_line_filters") or {}),
            extends=data.get("extends"),
        )


@dataclass(frozen=True)
class RedactionRule:
    name: str
    pattern: str
    replacement: str
    flags: str = "IGNORECASE"  # comma-separated re flag names


@dataclass(frozen=True)
class RedactionProfile:
    rules: tuple[RedactionRule, ...] = ()
    extends: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RedactionProfile":
        data = data or {}
        rules: list[RedactionRule] = []
        for raw in data.get("rules") or []:
            if not isinstance(raw, dict):
                continue
            rules.append(
                RedactionRule(
                    name=str(raw.get("name") or f"rule{len(rules)}"),
                    pattern=str(raw["pattern"]),
                    replacement=str(raw.get("replacement") or ""),
                    flags=str(raw.get("flags") or "IGNORECASE"),
                )
            )
        return cls(rules=tuple(rules), extends=data.get("extends"))


@dataclass(frozen=True)
class PromptProfile:
    platform_description: str = (
        "a generic application stack observed via Prometheus and Loki"
    )
    tool_run_hints: str = (
        "Prefer copy-pasteable curls against Prometheus (:9090) and Loki (:3100), "
        "plus docker / docker compose inspection commands when containers are involved."
    )
    extends: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "PromptProfile":
        data = data or {}
        return cls(
            platform_description=str(
                data.get("platform_description") or cls.platform_description
            ).strip(),
            tool_run_hints=str(data.get("tool_run_hints") or cls.tool_run_hints).strip(),
            extends=data.get("extends"),
        )
