# Implementation Spec — #50 Sandboxed runbook runner (Docker + allowlist)

> **Read this whole file before writing code.** It is written to be followed literally.
> Reference code blocks are near copy-paste ready; keep the names, signatures, and file
> paths exactly as written unless a linter forces a trivial change.

| | |
|---|---|
| **Issue** | [#50](https://github.com/mskrado/diagnostic-agent/issues/50) |
| **Depends on** | Nothing (this issue establishes the shared execution foundation). |
| **Blocks** | #51, #52, #53 |
| **Branch to create** | `feature/sandbox-runner-50` off `devel` |
| **Draft PR base** | `devel` · title `[core] Sandboxed runbook runner (Docker + allowlist) (#50)` · body `Closes #50` |
| **Overview / gate** | `docs/design/sandboxed-execution.md` (must be merged first) |

---

## 1. Goal (one sentence)

Add a module that runs **one pre-approved ("allowlisted") action** as an argv array inside a locked-down
Docker container and returns a structured result, refusing (fail-closed) anything not on the allowlist.

## 2. What you are NOT doing

- NOT touching the LangGraph graph (`build.py`/`nodes.py`). That is #52.
- NOT deciding *when* to run an action. That is #52.
- NOT classifying destructive actions. That is #51.
- NOT running shell strings. Actions are argv **lists**; never build a shell command string.

---

## 3. Files to create / modify (exact paths)

**Create:**

1. `app/execution/__init__.py`  (empty file — just makes the package importable)
2. `app/execution/sandbox.py`  (the runner)
3. `tests/test_sandbox.py`  (tests)

**Modify:**

4. `app/config.py`  — add the config fields in §4.
5. `app/profile/models.py`  — add the `ExecutionProfile` dataclasses in §5.
6. `app/profile/loader.py`  — load `execution_profile.yaml` and put it on `IntegrationProfile` (§6).
7. `app/profile/__init__.py`  — export `ExecutionProfile` (add to the import list and `__all__`).

Also create the example allowlist file so tests + reviewers have something concrete:

8. `examples/spring-modular-monolith/execution_profile.yaml`  (see §7)

---

## 4. Config fields (add to `app/config.py`, inside `class Settings`)

Add these fields **verbatim** near the other sections (e.g. after the `# --- Delivery ---` block):

```python
    # --- Execution (Track B; default OFF, opt-in per host) ---
    # Master switch. When false, the sandbox refuses to run (defense in depth;
    # the graph branch is also gated in #52).
    exec_enabled: bool = False
    # Path to execution_profile.yaml. Empty -> resolve from the active profile dir.
    exec_profile_path: str = ""
```

Add this method to `Settings` (mirrors `resolved_service_map_path`):

```python
    def resolved_exec_profile_path(self) -> str:
        """Path to execution_profile.yaml, or "" when the profile has none."""
        if self.exec_profile_path:
            return self.exec_profile_path
        from .profile import get_profile

        return getattr(get_profile(), "execution_profile_path", "") or ""
```

> Note: the verify-loop config (`exec_verify_*`) is added by #53, not here. Only add what this issue needs.

---

## 5. Profile data model (add to `app/profile/models.py`)

Follow the existing dataclass + `from_dict` style used by `MetricsProfile` etc. Add:

```python
@dataclass(frozen=True)
class ActionParam:
    name: str
    type: str = "string"          # "string" | "enum"
    values: tuple[str, ...] = ()  # allowed values when type == "enum"
    source: str = ""              # binding, e.g. "incident.service"; "" = must be literal

    @classmethod
    def from_dict(cls, name: str, data: dict) -> "ActionParam":
        return cls(
            name=name,
            type=str(data.get("type", "string")),
            values=tuple(str(v) for v in (data.get("values") or [])),
            source=str(data.get("from", "")),
        )


@dataclass(frozen=True)
class AllowlistedAction:
    id: str
    description: str
    argv: tuple[str, ...]
    params: tuple[ActionParam, ...]
    scope_services: tuple[str, ...]
    destructive: bool
    timeout_s: int

    @classmethod
    def from_dict(cls, data: dict) -> "AllowlistedAction":
        params_raw = data.get("params") or {}
        params = tuple(
            ActionParam.from_dict(pname, pdata or {})
            for pname, pdata in params_raw.items()
        )
        scope = (data.get("scope") or {}).get("services") or []
        return cls(
            id=str(data["id"]),
            description=str(data.get("description", "")),
            argv=tuple(str(a) for a in (data.get("argv") or [])),
            params=params,
            scope_services=tuple(str(s) for s in scope),
            destructive=bool(data.get("destructive", False)),
            timeout_s=int(data.get("timeout_s", 60)),
        )


@dataclass(frozen=True)
class ExecutionProfile:
    version: int
    image: str
    actions: tuple[AllowlistedAction, ...]

    @classmethod
    def from_dict(cls, data: dict) -> "ExecutionProfile":
        actions = tuple(
            AllowlistedAction.from_dict(a) for a in (data.get("actions") or [])
        )
        return cls(
            version=int(data.get("version", 1)),
            image=str(data.get("image", "")),
            actions=actions,
        )

    def get(self, action_id: str) -> "AllowlistedAction | None":
        for a in self.actions:
            if a.id == action_id:
                return a
        return None
```

---

## 6. Loader wiring (`app/profile/loader.py`)

1. Import the new model at the top:
   ```python
   from .models import (
       ExecutionProfile,  # add this
       LogsProfile, MetricsProfile, PromptProfile, RedactionProfile,
   )
   ```
2. Add two fields to the `IntegrationProfile` dataclass (both with defaults so existing constructions keep working):
   ```python
       execution: ExecutionProfile = ExecutionProfile(version=1, image="", actions=())
       execution_profile_path: str | None = None
   ```
3. Inside `build_profile`, after the `runbooks_path` resolution block, load the file **directly from the
   profile dir** (execution actions are host-only; they do NOT inherit from presets):
   ```python
   execution_data: dict = {}
   execution_profile_path: str | None = None
   if root is not None:
       exec_file = root / "execution_profile.yaml"
       if exec_file.is_file():
           try:
               execution_data = _read_yaml(exec_file)
               execution_profile_path = str(exec_file)
           except ProfileLoadError as exc:
               logger.error("%s", exc)
               load_errors.append(str(exc))
   ```
4. Pass both into the `IntegrationProfile(...)` return:
   ```python
       execution=ExecutionProfile.from_dict(execution_data),
       execution_profile_path=execution_profile_path,
   ```
5. In `app/profile/__init__.py` add `ExecutionProfile` to both the `from .models import (...)` line and `__all__`.

---

## 7. Example allowlist file (`examples/spring-modular-monolith/execution_profile.yaml`)

```yaml
version: 1
image: "ghcr.io/mskrado/diagnostic-agent-sandbox:1"
actions:
  - id: clear-cdn-cache
    description: "Purge the CDN edge cache for the affected service"
    argv: ["cache-purge", "--service", "{service}", "--scope", "edge"]
    params:
      service:
        type: enum
        from: "incident.service"
        values: ["web-gateway", "media-service"]
    scope:
      services: ["web-gateway", "media-service"]
    timeout_s: 60
  - id: restart-worker-pool
    description: "Rolling restart of the stateless worker pool"
    argv: ["scale", "restart", "--pool", "{pool}", "--rolling"]
    params:
      pool:
        type: enum
        values: ["ingest-workers", "render-workers"]
    destructive: true
    scope:
      services: ["worker-pool"]
    timeout_s: 180
```

---

## 8. The runner (`app/execution/sandbox.py`) — reference implementation

```python
"""Sandboxed execution of allowlisted runbook actions.

Runs ONE allowlisted action as an argv array inside a disposable, locked-down
Docker container. Knows nothing about runbooks or the graph. Fail-closed:
unknown actions / invalid params never start a container.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

from ..config import settings
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
    exit_code: int          # 0 ok; >0 failure; <0 killed/timeout
    stdout: str             # already redacted
    stderr: str             # already redacted
    duration_s: float
    denied: bool = False
    denial_reason: str | None = None


def _validate_and_render_argv(
    action: AllowlistedAction, params: dict, *, service: str
) -> tuple[list[str] | None, str | None]:
    """Return (argv, None) when valid, or (None, reason) when the action is denied.

    Parameters are substituted into argv tokens of the exact form "{name}".
    Never uses a shell; substitution is literal token replacement only.
    """
    resolved: dict[str, str] = {}
    for spec in action.params:
        # Bind from incident context when declared, else require an explicit value.
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

    # Scope check: any bound service-like value must be inside scope_services.
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
        # Image comes from the execution profile unless explicitly overridden.
        self._image = image

    @property
    def image(self) -> str:
        if self._image:
            return self._image
        return get_profile().execution.image

    def _docker_argv(self, inner_argv: list[str], timeout_s: int) -> list[str]:
        """Build the locked-down `docker run` command. No network, no mounts, no secrets."""
        return [
            "docker", "run", "--rm",
            "--network", "none",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--read-only",
            "--pids-limit", "128",
            "--memory", "256m",
            "--cpus", "1",
            "--user", "65534:65534",   # nobody
            self.image,
            *inner_argv,
        ]

    def run(self, action_id: str, params: dict, *, service: str) -> ActionResult:
        if not settings.exec_enabled:
            raise ExecutionDisabled("AGENT_EXEC_ENABLED is false")

        action = get_profile().execution.get(action_id)
        if action is None:
            return ActionResult(
                action_id=action_id, argv=[], exit_code=1, stdout="", stderr="",
                duration_s=0.0, denied=True,
                denial_reason=f"unknown action id {action_id!r}",
            )

        argv, reason = _validate_and_render_argv(action, params, service=service)
        if argv is None:
            return ActionResult(
                action_id=action_id, argv=[], exit_code=1, stdout="", stderr="",
                duration_s=0.0, denied=True, denial_reason=reason,
            )

        cmd = self._docker_argv(argv, action.timeout_s)
        logger.info("sandbox run action=%s service=%s argv=%s", action_id, service, argv)
        import time
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
            # docker not installed / not on PATH
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
```

---

## 9. Step-by-step checklist

1. Create branch `feature/sandbox-runner-50` off `devel`.
2. Add config fields + method (§4).
3. Add profile models (§5).
4. Wire loader + `__init__` exports (§6).
5. Create `examples/.../execution_profile.yaml` (§7).
6. Create `app/execution/__init__.py` (empty) and `app/execution/sandbox.py` (§8).
7. Write tests (§10). Run `pytest -q`.
8. Commit with `git commit -s`. Push. Open draft PR (base `devel`, `Closes #50`).

---

## 10. Tests (`tests/test_sandbox.py`) — write all of these

Use `monkeypatch` for `docker` by patching `subprocess.run`. Follow the conftest reset pattern.
The conftest already pins the spring example profile, which now has `execution_profile.yaml`.

```python
import subprocess
import types

import pytest

from app import config as config_mod
from app.execution.sandbox import Sandbox, ActionResult, ExecutionDisabled


def _enable_exec(monkeypatch):
    monkeypatch.setenv("AGENT_EXEC_ENABLED", "true")
    config_mod.settings = config_mod.Settings()
    from app.profile import reset_profile_cache
    reset_profile_cache()


def _fake_completed(returncode=0, stdout="ok", stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_run_raises_when_exec_disabled(monkeypatch):
    monkeypatch.setenv("AGENT_EXEC_ENABLED", "false")
    config_mod.settings = config_mod.Settings()
    from app.profile import reset_profile_cache
    reset_profile_cache()
    sb = Sandbox()
    with pytest.raises(ExecutionDisabled):
        sb.run("clear-cdn-cache", {"service": "web-gateway"}, service="web-gateway")


def test_unknown_action_is_denied(monkeypatch):
    _enable_exec(monkeypatch)
    sb = Sandbox()
    res = sb.run("does-not-exist", {}, service="web-gateway")
    assert res.denied is True
    assert "unknown action" in res.denial_reason


def test_enum_param_outside_allowed_is_denied(monkeypatch):
    _enable_exec(monkeypatch)
    sb = Sandbox()
    res = sb.run("clear-cdn-cache", {"service": "web-gateway"}, service="not-in-scope")
    assert res.denied is True


def test_allowlisted_action_runs(monkeypatch):
    _enable_exec(monkeypatch)
    calls = {}

    def fake_run(cmd, capture_output, text, timeout):
        calls["cmd"] = cmd
        return _fake_completed(returncode=0, stdout="purged", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    sb = Sandbox()
    res = sb.run("clear-cdn-cache", {"service": "web-gateway"}, service="web-gateway")
    assert res.denied is False
    assert res.exit_code == 0
    assert res.stdout == "purged"
    # argv rendered with the bound service, no shell, no braces left
    assert "web-gateway" in res.argv
    assert all("{" not in tok for tok in res.argv)
    # locked-down docker flags present
    assert "--network" in calls["cmd"] and "none" in calls["cmd"]
    assert "--cap-drop" in calls["cmd"]


def test_timeout_returns_negative_exit(monkeypatch):
    _enable_exec(monkeypatch)

    def fake_run(cmd, capture_output, text, timeout):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    monkeypatch.setattr(subprocess, "run", fake_run)
    sb = Sandbox()
    res = sb.run("clear-cdn-cache", {"service": "web-gateway"}, service="web-gateway")
    assert res.exit_code < 0
```

> If the spring example profile can't carry an `execution_profile.yaml` for policy reasons, instead build
> a profile in the test with `build_profile(profile_dir=<tmp>)` and monkeypatch `get_profile`. The env-pin
> approach above is preferred because it exercises the real loader wiring you added in §6.

---

## 11. Definition of done (must all be true)

- [ ] `AGENT_EXEC_ENABLED=false` (default) → `Sandbox.run` raises `ExecutionDisabled`.
- [ ] Unknown action id → `denied=True`, no `subprocess.run` call.
- [ ] Param outside enum / service outside scope → `denied=True`.
- [ ] Valid action → `docker run` argv includes `--network none`, `--cap-drop ALL`, `--read-only`, and the rendered inner argv with no `{...}` left.
- [ ] stdout/stderr pass through `redact_text()`.
- [ ] Timeout → negative exit code, no crash.
- [ ] `pytest -q` green; commit DCO-signed; draft PR open with base `devel`.
