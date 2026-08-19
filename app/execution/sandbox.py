"""Sandboxed execution of allowlisted runbook actions.

Runs one allowlisted action as an argv array inside a disposable, locked-down
Docker container. Knows nothing about runbooks or the graph. Fail-closed:
unknown actions / invalid params never start a container.
"""
from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass

from .. import config as config_mod
from ..delivery.redact import redact_text
from ..profile import get_profile
from ..profile.models import AllowlistedAction

logger = logging.getLogger(__name__)


class ExecutionDisabled(RuntimeError):
    """Raised when Sandbox.run is called while AGENT_EXEC_ENABLED is false."""


@dataclass
class ActionResult:
    action_id: str
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    denied: bool = False
    denial_reason: str | None = None


def _validate_and_render_argv(
    action: AllowlistedAction, params: dict, *, service: str
) -> tuple[list[str] | None, str | None]:
    """Return (argv, None) when valid, or (None, reason) when denied."""
    resolved: dict[str, str] = {}
    for spec in action.params:
        if spec.source == "incident.service":
            value = service
        else:
            value = params.get(spec.name)
        if value is None:
            return None, f"missing required param '{spec.name}'"
        value = str(value)
        if spec.type == "enum" and spec.values and value not in spec.values:
            return None, f"param '{spec.name}'={value!r} not in allowed {list(spec.values)}"
        resolved[spec.name] = value

    if action.scope_services and service not in action.scope_services:
        return None, f"service {service!r} outside action scope {list(action.scope_services)}"

    argv: list[str] = []
    for token in action.argv:
        if token.startswith("{") and token.endswith("}"):
            key = token[1:-1]
            if key not in resolved:
                return None, f"argv references unknown param '{key}'"
            argv.append(resolved[key])
        else:
            argv.append(token)
    return argv, None


class Sandbox:
    """Executes allowlisted actions in Docker. One instance per agent process."""

    def __init__(self, image: str | None = None):
        self._image = image

    @property
    def image(self) -> str:
        if self._image:
            return self._image
        return get_profile().execution.image

    def _docker_argv(self, inner_argv: list[str]) -> list[str]:
        return [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--pids-limit",
            "128",
            "--memory",
            "256m",
            "--cpus",
            "1",
            "--user",
            "65534:65534",
            self.image,
            *inner_argv,
        ]

    def run(self, action_id: str, params: dict, *, service: str) -> ActionResult:
        if not config_mod.settings.exec_enabled:
            raise ExecutionDisabled("AGENT_EXEC_ENABLED is false")

        action = get_profile().execution.get(action_id)
        if action is None:
            return ActionResult(
                action_id=action_id,
                argv=[],
                exit_code=1,
                stdout="",
                stderr="",
                duration_s=0.0,
                denied=True,
                denial_reason=f"unknown action id {action_id!r}",
            )

        argv, reason = _validate_and_render_argv(action, params, service=service)
        if argv is None:
            return ActionResult(
                action_id=action_id,
                argv=[],
                exit_code=1,
                stdout="",
                stderr="",
                duration_s=0.0,
                denied=True,
                denial_reason=reason,
            )

        cmd = self._docker_argv(argv)
        logger.info("sandbox run action=%s service=%s argv=%s", action_id, service, argv)
        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=action.timeout_s,
            )
            exit_code = proc.returncode
            stdout, stderr = proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            exit_code = -1
            stdout = exc.stdout or ""
            stderr = (exc.stderr or "") + f"\n[timeout after {action.timeout_s}s]"
        except FileNotFoundError:
            exit_code = -2
            stdout, stderr = "", "docker executable not found"
        duration = time.monotonic() - start

        return ActionResult(
            action_id=action_id,
            argv=argv,
            exit_code=exit_code,
            stdout=redact_text(stdout or ""),
            stderr=redact_text(stderr or ""),
            duration_s=round(duration, 3),
            denied=False,
            denial_reason=None,
        )
