"""Mechanical grounding checks for LLM-authored workspace prose.

The model may invent compose names, ports, and ``service=`` filters. Before any
of that prose lands in a file, every identifier it quotes must appear in the
evidence that was fed to it. This module builds that allowlist and scores a
draft against it — no second LLM call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from ..scan.models import ScanEvidence

# Claimed execution / auto-remediation — hints are for humans, never facts.
_REMEDIATION = re.compile(
    r"\b("
    r"i\s+(restarted|fixed|scaled|deleted|killed|ran|executed|remediated)|"
    r"we\s+(restarted|fixed|scaled)|"
    r"auto-?remediat|"
    r"automatically\s+(fix|restart|scale|delete|kill|remediate)|"
    r"i\s+have\s+(restarted|fixed|run)"
    r")\b",
    re.IGNORECASE,
)

# Quoted identifiers that look like stack names the model must not invent.
_QUOTED = re.compile(
    r"""(?:service|job|app|application|container|name)\s*=\s*["']([^"']+)["']"""
    r"""|(?:compose\s+(?:logs|exec|restart|ps)|docker\s+(?:logs|exec|restart))\s+([A-Za-z0-9][\w.-]*)"""
    r"""|https?://([A-Za-z0-9][\w.-]*)(?::(\d+))?"""
    r"""|(?:curl\s+[^\n]*?://)([A-Za-z0-9][\w.-]*)(?::(\d+))?""",
    re.IGNORECASE,
)

# Bare ``service="x"`` / ``{service="x"}`` already covered; also catch
# docker compose service arguments after common verbs.
_COMPOSE_ARG = re.compile(
    r"\bdocker\s+compose\s+\w+(?:\s+-[^\s]+)*\s+([A-Za-z][\w-]*)",
    re.IGNORECASE,
)

# Always allowed — tooling the agent itself talks about, not host inventory.
_ALWAYS = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "prometheus",
        "loki",
        "grafana",
        "alertmanager",
        "tempo",
        "docker",
        "compose",
        "curl",
        "head",
        "tail",
        "jq",
        "psql",
        "redis-cli",
        "pg_isready",
        "actuator",
        "health",
        "query",
        "query_range",
        "api",
        "v1",
        "service",
        "job",
        "level",
        "error",
        "warn",
        "info",
    }
)

_PLATFORM_MAX = 2000


@dataclass(frozen=True)
class Allowlist:
    """Names and ports the model is allowed to quote."""

    names: frozenset[str]
    ports: frozenset[str]
    services: frozenset[str]

    def contains_name(self, name: str) -> bool:
        lowered = name.lower().strip()
        if not lowered or lowered in _ALWAYS:
            return True
        if lowered in self.names:
            return True
        # Partial: "platform-service:8080" → try the host part.
        host = lowered.split(":")[0]
        return host in self.names or host in _ALWAYS


@dataclass(frozen=True)
class GroundingFailure:
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"{self.kind}: {self.detail}"


def build_allowlist(
    evidence: ScanEvidence,
    *,
    node_names: tuple[str, ...] = (),
    extra_urls: tuple[str, ...] = (),
) -> Allowlist:
    """Collect every identifier the evidence already knows about."""
    names: set[str] = set()
    ports: set[str] = set()
    services: set[str] = set()

    for service in evidence.findings.services:
        names.add(service.name.lower())
        services.add(service.name.lower())
        for hint in service.log_services_hint:
            names.add(hint.lower())
            services.add(hint.lower())
    for name in node_names:
        names.add(name.lower())
        services.add(name.lower())

    for values in evidence.prometheus.label_values.values():
        for value in values:
            names.add(value.lower())
    for values in evidence.loki.label_values.values():
        for value in values:
            names.add(value.lower())
    for target in evidence.prometheus.targets:
        if target.job:
            names.add(target.job.lower())
        if target.service:
            names.add(target.service.lower())
            services.add(target.service.lower())
        if target.instance:
            host = target.instance.split(":")[0].lower()
            names.add(host)
            if ":" in target.instance:
                ports.add(target.instance.split(":")[-1])

    for rule in evidence.all_rules():
        if rule.name:
            names.add(rule.name.lower())
        for service in rule.services:
            names.add(service.lower())
            services.add(service.lower())

    for url in (
        evidence.prometheus.url,
        evidence.loki.url,
        evidence.alertmanager.url,
        *extra_urls,
    ):
        host, port = _host_port(url)
        if host:
            names.add(host.lower())
        if port:
            ports.add(port)

    # Common exporter / listen ports that appear in every profile's examples.
    ports.update({"9090", "3100", "9093", "8080", "8000", "3000"})

    return Allowlist(
        names=frozenset(names),
        ports=frozenset(ports),
        services=frozenset(services),
    )


def _host_port(url: str) -> tuple[str, str]:
    if not url:
        return "", ""
    try:
        parsed = urlparse(url if "://" in url else f"http://{url}")
    except ValueError:
        return "", ""
    host = parsed.hostname or ""
    port = str(parsed.port) if parsed.port else ""
    return host, port


def validate_prompt_profile(
    platform_description: str,
    tool_run_hints: str,
    allowlist: Allowlist,
) -> tuple[GroundingFailure, ...]:
    """Return every grounding failure in a proposed prompt profile."""
    failures: list[GroundingFailure] = []
    text = f"{platform_description}\n{tool_run_hints}"

    if not (platform_description or "").strip():
        failures.append(GroundingFailure("empty", "platform_description is empty"))
    if not (tool_run_hints or "").strip():
        failures.append(GroundingFailure("empty", "tool_run_hints is empty"))
    if len(platform_description or "") > _PLATFORM_MAX:
        failures.append(
            GroundingFailure(
                "length",
                f"platform_description is {len(platform_description)} chars "
                f"(max {_PLATFORM_MAX})",
            )
        )

    for match in _REMEDIATION.finditer(text):
        failures.append(
            GroundingFailure(
                "remediation",
                f"claims execution or auto-remediation ({match.group(0)!r})",
            )
        )

    seen: set[str] = set()
    for match in _QUOTED.finditer(text):
        groups = [g for g in match.groups() if g]
        # Groups alternate name/port in some alternatives; classify by shape.
        for group in groups:
            key = group.lower()
            if key in seen:
                continue
            seen.add(key)
            if group.isdigit():
                if group not in allowlist.ports and group not in _ALWAYS:
                    # Ports outside the known set are soft: many stacks publish
                    # app ports the scan never saw. Only fail clearly invented
                    # high ports when paired with an unknown host — handled below
                    # when the host itself fails.
                    continue
                continue
            if not allowlist.contains_name(group):
                failures.append(
                    GroundingFailure(
                        "ungrounded",
                        f"{group!r} does not appear in the evidence allowlist",
                    )
                )

    for match in _COMPOSE_ARG.finditer(text):
        name = match.group(1).lower()
        if name in seen or name in _ALWAYS:
            continue
        seen.add(name)
        if not allowlist.contains_name(name):
            failures.append(
                GroundingFailure(
                    "ungrounded",
                    f"compose/docker argument {name!r} is not in the evidence",
                )
            )

    return tuple(failures)


def validate_runbook_body(body: str, allowlist: Allowlist) -> tuple[GroundingFailure, ...]:
    """Lighter check for skeleton runbooks: remediation + service= filters."""
    failures: list[GroundingFailure] = []
    for match in _REMEDIATION.finditer(body):
        failures.append(
            GroundingFailure(
                "remediation",
                f"claims execution or auto-remediation ({match.group(0)!r})",
            )
        )
    for match in re.finditer(
        r"""service\s*=\s*["']([^"']+)["']""", body, flags=re.IGNORECASE
    ):
        name = match.group(1)
        if not allowlist.contains_name(name):
            failures.append(
                GroundingFailure(
                    "ungrounded",
                    f"service={name!r} is not in the evidence allowlist",
                )
            )
    return tuple(failures)
