"""Optional --apply / --start helpers for a generated install bundle."""
from __future__ import annotations

import subprocess
from pathlib import Path

import httpx

from .models import InstallParams


def apply_reloads(params: InstallParams) -> list[str]:
    """Best-effort Prometheus / Alertmanager config reload. Returns notes."""
    notes: list[str] = []
    for label, url in (
        ("prometheus", params.prometheus_url),
        ("alertmanager", params.alertmanager_url),
    ):
        if not url:
            notes.append(f"{label}: skipped (no URL)")
            continue
        reload_url = url.rstrip("/") + "/-/reload"
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.post(reload_url)
            if resp.status_code < 400:
                notes.append(f"{label}: reload OK ({reload_url})")
            else:
                notes.append(f"{label}: reload HTTP {resp.status_code} -- apply files manually")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{label}: reload failed ({exc}) -- apply files manually")
    return notes


def start_agent(output: Path) -> tuple[int, str]:
    """``docker compose up -d`` in ``output/agent``. Returns (rc, message)."""
    agent_dir = output / "agent"
    if not (agent_dir / "docker-compose.yml").is_file():
        return 2, "missing agent/docker-compose.yml"
    try:
        proc = subprocess.run(
            ["docker", "compose", "--env-file", ".env", "up", "-d"],
            cwd=str(agent_dir),
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 2, f"docker compose failed: {exc}"
    if proc.returncode != 0:
        return proc.returncode, proc.stderr.strip() or proc.stdout.strip()
    return 0, proc.stdout.strip() or "agent started"


def health_check(params: InstallParams, *, timeout: float = 5.0) -> tuple[bool, str]:
    url = f"http://127.0.0.1:{params.agent_host_port}/health"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url)
        if resp.status_code < 400:
            return True, f"health OK {url}"
        return False, f"health HTTP {resp.status_code} {url}"
    except Exception as exc:  # noqa: BLE001
        return False, f"health failed: {exc}"
