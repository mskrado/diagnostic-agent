"""Discover observability tools on a target host.

Layers (best-effort, confidence-ranked):

1. Docker introspection (local or via ``--ssh``)
2. HTTP health/version probes (docker DNS + host ports + remote base)
3. Bounded host-port scan of well-known ports
"""
from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from .models import (
    DEFAULT_PORTS,
    HEALTH_PATHS,
    IMAGE_HINTS,
    AddressingMode,
    DiscoveryReport,
    ReachabilityMatrix,
    ToolEndpoint,
    ToolKind,
)
from .progress import NullDiscoveryProgress


class _Progress(Protocol):
    def start(self) -> None: ...
    def phase(self, name: str) -> None: ...
    def ensure_tools(self, kinds: list[ToolKind]) -> None: ...
    def found_container(self, kind: ToolKind, container_name: str) -> None: ...
    def probing(self, kind: ToolKind, url: str) -> None: ...
    def result(
        self,
        kind: ToolKind,
        *,
        reachable: bool,
        url: str = "",
        version: str = "",
    ) -> None: ...
    def finish(self, *, placement: str = "") -> None: ...
    def close(self) -> None: ...


# Tools always shown on the status chart (even before Docker finds them).
_CHART_KINDS: tuple[ToolKind, ...] = (
    ToolKind.PROMETHEUS,
    ToolKind.LOKI,
    ToolKind.ALERTMANAGER,
    ToolKind.GRAFANA,
    ToolKind.TEMPO,
    ToolKind.OLLAMA,
    ToolKind.MAILPIT,
    ToolKind.NODE_EXPORTER,
    ToolKind.CADVISOR,
)


def discover(
    *,
    target: str = "local",
    ssh: str | None = None,
    timeout: float = 3.0,
    progress: _Progress | None = None,
) -> DiscoveryReport:
    """Run full discovery and return a populated :class:`DiscoveryReport`."""
    progress = progress or NullDiscoveryProgress()
    report = DiscoveryReport(target=target)
    progress.start()
    try:
        progress.phase("docker introspection")
        progress.ensure_tools(list(_CHART_KINDS))
        containers = _discover_docker_containers(ssh=ssh)
        if containers:
            report.decisions.append(
                f"docker introspection: {len(containers)} running container(s)"
                + (f" via ssh {ssh}" if ssh else " (local)")
            )
            tools = _tools_from_containers(containers)
            report.tools.extend(tools)
            for tool in tools:
                progress.found_container(tool.kind, tool.container_name)
            progress.phase(f"docker: {len(containers)} container(s)")
        else:
            report.warnings.append(
                "docker introspection unavailable -- falling back to HTTP/port probes"
            )
            progress.phase("docker unavailable — HTTP probes")

        _probe_http_layers(report, target=target, timeout=timeout, progress=progress)
        _fill_missing_from_port_scan(
            report, target=target, timeout=timeout, progress=progress
        )
        # Anything still not marked reachable is a miss.
        for tool in report.tools:
            if not tool.reachable:
                progress.result(tool.kind, reachable=False)
        for kind in _CHART_KINDS:
            if report.tool(kind) is None:
                progress.result(kind, reachable=False)

        report.reachability = _build_reachability(report, target=target)
        _validate_minimum(report)
        progress.finish(placement=report.reachability.agent_placement)
    except Exception:
        progress.close()
        raise
    return report


# ---------------------------------------------------------------------------
# Docker introspection
# ---------------------------------------------------------------------------
def _discover_docker_containers(*, ssh: str | None) -> list[dict[str, Any]]:
    fmt = "{{json .}}"
    cmd = ["docker", "ps", "--format", fmt]
    if ssh:
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", ssh, "--", *cmd]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, check=False
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if proc.returncode != 0:
        return []

    containers: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            containers.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return containers


def _tools_from_containers(containers: list[dict[str, Any]]) -> list[ToolEndpoint]:
    tools: list[ToolEndpoint] = []
    seen: set[ToolKind] = set()
    for c in containers:
        image = (c.get("Image") or c.get("image") or "").lower()
        name = (c.get("Names") or c.get("names") or "").lstrip("/")
        # docker ps --format json uses "Names" as a single string sometimes,
        # or "Names" with commas when --format={{.Names}}.
        if "," in name:
            name = name.split(",")[0]
        ports_raw = c.get("Ports") or c.get("ports") or ""
        networks = _extract_networks(c)
        kind = _match_image(image)
        if kind is None or kind in seen:
            continue
        seen.add(kind)
        published = _parse_published_port(ports_raw, DEFAULT_PORTS.get(kind))
        tools.append(
            ToolEndpoint(
                kind=kind,
                container_name=name,
                docker_network=networks[0] if networks else "",
                published_port=published,
                confidence="medium",
                notes=[f"image={image}"],
            )
        )
    return tools


def _match_image(image: str) -> ToolKind | None:
    for hint, kind in IMAGE_HINTS:
        if hint in image:
            return kind
    return None


def _extract_networks(container: dict[str, Any]) -> list[str]:
    # `docker ps --format {{json .}}` does not include Networks; try inspect-like
    # fields if present. Otherwise leave empty -- probing will still work on ports.
    nets = container.get("Networks") or container.get("networks")
    if isinstance(nets, str) and nets.strip():
        return [n.strip() for n in nets.split(",") if n.strip()]
    if isinstance(nets, dict):
        return list(nets.keys())
    return []


_PORT_RE = re.compile(
    r"(?:0\.0\.0\.0|127\.0\.0\.1|\[::\])?:(\d+)->(\d+)/tcp"
)


def _parse_published_port(ports_raw: str, preferred_container_port: int | None) -> int | None:
    matches = _PORT_RE.findall(ports_raw or "")
    if not matches:
        return None
    if preferred_container_port is not None:
        for host_port, container_port in matches:
            if int(container_port) == preferred_container_port:
                return int(host_port)
    return int(matches[0][0])


# ---------------------------------------------------------------------------
# HTTP probing
# ---------------------------------------------------------------------------
def _probe_http_layers(
    report: DiscoveryReport,
    *,
    target: str,
    timeout: float,
    progress: _Progress,
) -> None:
    """Probe existing tool entries and create host/remote candidates."""
    host = _target_host(target)

    # Ensure we have candidate entries for every critical tool.
    for kind in (
        ToolKind.PROMETHEUS,
        ToolKind.LOKI,
        ToolKind.ALERTMANAGER,
        ToolKind.GRAFANA,
        ToolKind.TEMPO,
        ToolKind.OLLAMA,
        ToolKind.MAILPIT,
    ):
        if report.tool(kind) is None:
            report.tools.append(ToolEndpoint(kind=kind))

    progress.ensure_tools([t.kind for t in report.tools])

    for tool in list(report.tools):
        paths = HEALTH_PATHS.get(tool.kind, [])
        if not paths:
            continue

        candidates: list[tuple[str, AddressingMode]] = []
        # Prefer docker DNS when we know a container name.
        if tool.container_name:
            port = DEFAULT_PORTS.get(tool.kind, 80)
            candidates.append(
                (f"http://{tool.container_name}:{port}", AddressingMode.DOCKER_DNS)
            )
        if tool.published_port:
            candidates.append(
                (f"http://{host}:{tool.published_port}", AddressingMode.HOST_PORT)
            )
        # Default well-known port on the target host.
        default_port = DEFAULT_PORTS.get(tool.kind)
        if default_port:
            candidates.append(
                (f"http://{host}:{default_port}", AddressingMode.HOST_PORT)
            )
        if target not in ("local", "localhost", "127.0.0.1") and default_port:
            candidates.append(
                (f"http://{host}:{default_port}", AddressingMode.REMOTE_HTTP)
            )

        # Deduplicate while preserving order.
        seen_urls: set[str] = set()
        reached = False
        for url, mode in candidates:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            progress.probing(tool.kind, url)
            ok, version, detail = _http_probe(url, paths, timeout=timeout)
            if ok:
                tool.reachable = True
                tool.url = url
                tool.addressing_mode = mode
                tool.version = version
                tool.confidence = "high"
                tool.notes.append(detail)
                progress.result(
                    tool.kind, reachable=True, url=url, version=version
                )
                reached = True
                break
            tool.notes.append(detail)
        if not reached:
            # Leave as waiting — port scan / finish will mark not-found.
            progress.probing(tool.kind, "(no answer yet)")


def _http_probe(
    base_url: str, paths: list[str], *, timeout: float
) -> tuple[bool, str, str]:
    version = ""
    last_err = ""
    for path in paths:
        url = base_url.rstrip("/") + path
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                resp = client.get(url)
        except Exception as exc:  # noqa: BLE001 - surface any probe failure
            last_err = f"{path}: {type(exc).__name__}: {exc}"
            continue
        if resp.status_code >= 400:
            last_err = f"{path}: HTTP {resp.status_code}"
            continue
        # Try to extract a version string when JSON is available.
        try:
            body = resp.json()
            version = (
                body.get("version")
                or body.get("data", {}).get("version")
                or body.get("versionInfo", {}).get("version")
                or body.get("buildDate")
                or ""
            )
            if isinstance(version, dict):
                version = version.get("version", "") or ""
        except Exception:  # noqa: BLE001
            version = ""
        return True, str(version), f"ok {path} via {base_url}"
    return False, "", last_err or f"unreachable {base_url}"


def _fill_missing_from_port_scan(
    report: DiscoveryReport,
    *,
    target: str,
    timeout: float,
    progress: _Progress,
) -> None:
    """Last-chance: probe well-known ports that have no successful URL yet."""
    host = _target_host(target)
    progress.phase("port scan")
    for kind, port in DEFAULT_PORTS.items():
        tool = report.tool(kind)
        if tool and tool.reachable:
            continue
        if kind not in HEALTH_PATHS:
            continue
        url = f"http://{host}:{port}"
        progress.probing(kind, url)
        ok, version, detail = _http_probe(url, HEALTH_PATHS[kind], timeout=timeout)
        if not ok:
            continue
        if tool is None:
            tool = ToolEndpoint(kind=kind)
            report.tools.append(tool)
        tool.reachable = True
        tool.url = url
        tool.addressing_mode = (
            AddressingMode.REMOTE_HTTP
            if target not in ("local", "localhost", "127.0.0.1")
            else AddressingMode.HOST_PORT
        )
        tool.version = version
        tool.published_port = port
        tool.confidence = "medium"
        tool.notes.append(detail)
        progress.result(kind, reachable=True, url=url, version=version)


def _target_host(target: str) -> str:
    if target in ("local", "localhost", "127.0.0.1", ""):
        return "127.0.0.1"
    parsed = urlparse(target if "://" in target else f"http://{target}")
    return parsed.hostname or target


# ---------------------------------------------------------------------------
# Reachability matrix
# ---------------------------------------------------------------------------
def _build_reachability(report: DiscoveryReport, *, target: str) -> ReachabilityMatrix:
    matrix = ReachabilityMatrix()
    prom = report.tool(ToolKind.PROMETHEUS)
    loki = report.tool(ToolKind.LOKI)
    grafana = report.tool(ToolKind.GRAFANA)
    am = report.tool(ToolKind.ALERTMANAGER)

    # Prefer a shared docker network when multiple tools share one.
    networks = [
        t.docker_network
        for t in report.tools
        if t.reachable and t.docker_network
    ]
    shared = ""
    if networks:
        # Most common network wins.
        shared = max(set(networks), key=networks.count)
        matrix.shared_docker_network = shared

    same_network_count = sum(
        1
        for t in (prom, loki, grafana, am)
        if t and t.reachable and t.addressing_mode == AddressingMode.DOCKER_DNS
    )
    if same_network_count >= 2:
        matrix.agent_placement = "same_docker_network"
        matrix.notes.append(
            "Multiple tools reachable via Docker DNS -- prefer container DNS for agent"
        )
    elif target not in ("local", "localhost", "127.0.0.1"):
        matrix.agent_placement = "remote_target"
    else:
        matrix.agent_placement = "standalone_local"

    def _pick(tool: ToolEndpoint | None) -> str:
        if not tool or not tool.reachable:
            return ""
        if matrix.agent_placement == "same_docker_network" and tool.container_name:
            port = DEFAULT_PORTS.get(tool.kind, 80)
            return f"http://{tool.container_name}:{port}"
        return tool.url

    matrix.agent_to_prometheus = _pick(prom)
    matrix.agent_to_loki = _pick(loki)
    matrix.agent_to_grafana = _pick(grafana)
    matrix.agent_to_alertmanager = _pick(am)

    if matrix.agent_placement == "same_docker_network":
        matrix.alertmanager_to_agent_webhook = (
            f"http://{matrix.agent_container_name}:8000/alert"
        )
    elif matrix.agent_placement == "remote_target":
        host = _target_host(target)
        matrix.alertmanager_to_agent_webhook = (
            f"http://{host}:{matrix.agent_host_port}/alert"
        )
        matrix.notes.append(
            "Remote target: confirm the agent host is routable from Alertmanager"
        )
    else:
        # Alertmanager in Docker talking to an agent published on the host.
        matrix.alertmanager_to_agent_webhook = (
            f"http://host.docker.internal:{matrix.agent_host_port}/alert"
        )
        matrix.notes.append(
            "Standalone local: AM->agent uses host.docker.internal "
            f"(host port {matrix.agent_host_port})"
        )

    return matrix


def _validate_minimum(report: DiscoveryReport) -> None:
    prom = report.tool(ToolKind.PROMETHEUS)
    if not prom or not prom.reachable:
        report.errors.append(
            "Prometheus is required but was not reachable. "
            "Start Prometheus or pass an explicit --prometheus-url."
        )
    for kind, label in (
        (ToolKind.LOKI, "Loki"),
        (ToolKind.ALERTMANAGER, "Alertmanager"),
        (ToolKind.GRAFANA, "Grafana"),
    ):
        tool = report.tool(kind)
        if not tool or not tool.reachable:
            report.warnings.append(
                f"{label} not detected -- installer will degrade gracefully"
            )
