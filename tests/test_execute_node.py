from __future__ import annotations

from unittest.mock import MagicMock

from app.execution.runbook import ExecutableRunbook, RunbookStep
from app.execution.sandbox import ActionResult
from app.graph.nodes import DiagnosticNodes
from app.profile.models import ActionParam, AllowlistedAction


class _FakeSandbox:
    def __init__(self, result: ActionResult):
        self._result = result
        self.calls: list[tuple[str, str]] = []

    def run(self, action_id, params, *, service):
        self.calls.append((action_id, service))
        return self._result


def _nodes(sandbox):
    return DiagnosticNodes(None, None, None, None, None, None, sandbox=sandbox)


def _state(confidence="high"):
    return {
        "service": "web-gateway",
        "alert_type": "HighErrorRate",
        "hypotheses": {"confidence_note": confidence},
    }


def _runbook():
    return ExecutableRunbook(
        path="rb.md",
        alert_types=["HighErrorRate"],
        services=["web-gateway"],
        min_confidence="high",
        steps=[RunbookStep("clear-cdn-cache")],
    )


def _action(*, destructive=False):
    return AllowlistedAction(
        id="clear-cdn-cache",
        description="Refresh the CDN edge cache",
        argv=("cache-refresh", "--service", "{service}"),
        params=(
            ActionParam(
                name="service",
                type="enum",
                values=("web-gateway", "media-service"),
                source="incident.service",
            ),
        ),
        scope_services=("web-gateway", "media-service"),
        destructive=destructive,
        timeout_s=60,
    )


def _success_result():
    return ActionResult(
        action_id="clear-cdn-cache",
        argv=["cache-refresh", "--service", "web-gateway"],
        exit_code=0,
        stdout="ok",
        stderr="",
        duration_s=0.1,
        denied=False,
        denial_reason=None,
    )


def _patch_execute(monkeypatch, *, runbook=_runbook(), action=_action()):
    monkeypatch.setattr(
        "app.graph.nodes.load_executable_runbooks",
        lambda _path: [runbook] if runbook else [],
    )
    profile = MagicMock()
    profile.execution.get = lambda action_id: action if action_id == action.id else None
    monkeypatch.setattr("app.graph.nodes.get_profile", lambda: profile)


def test_success_path_routes_to_execute(monkeypatch):
    sandbox = _FakeSandbox(_success_result())
    _patch_execute(monkeypatch)
    out = _nodes(sandbox).execute_runbook(_state())
    assert out["route"] == "execute"
    assert out["execution_result"]["exit_code"] == 0
    assert sandbox.calls == [("clear-cdn-cache", "web-gateway")]


def test_denied_execution_escalates(monkeypatch):
    denied = ActionResult(
        action_id="clear-cdn-cache",
        argv=[],
        exit_code=1,
        stdout="",
        stderr="",
        duration_s=0.0,
        denied=True,
        denial_reason="unknown action",
    )
    sandbox = _FakeSandbox(denied)
    _patch_execute(monkeypatch)
    out = _nodes(sandbox).execute_runbook(_state())
    assert out["route"] == "escalate"
    assert out["outcome"] == "escalated"


def test_nonzero_exit_escalates(monkeypatch):
    failed = ActionResult(
        action_id="clear-cdn-cache",
        argv=["cache-refresh", "--service", "web-gateway"],
        exit_code=2,
        stdout="",
        stderr="failed",
        duration_s=0.1,
        denied=False,
        denial_reason=None,
    )
    sandbox = _FakeSandbox(failed)
    _patch_execute(monkeypatch)
    out = _nodes(sandbox).execute_runbook(_state())
    assert out["route"] == "escalate"
    assert out["outcome"] == "escalated"


def test_destructive_action_escalates_without_sandbox_call(monkeypatch):
    sandbox = _FakeSandbox(_success_result())
    _patch_execute(monkeypatch, action=_action(destructive=True))
    out = _nodes(sandbox).execute_runbook(_state())
    assert out["route"] == "escalate"
    assert out["outcome"] == "escalated"
    assert out["classifier_verdict"]["decision"] == "hold"
    assert sandbox.calls == []


def test_no_matching_runbook_escalates(monkeypatch):
    sandbox = _FakeSandbox(_success_result())
    _patch_execute(monkeypatch, runbook=None)
    out = _nodes(sandbox).execute_runbook(_state())
    assert out["route"] == "escalate"
    assert out["outcome"] == "escalated"
    assert sandbox.calls == []


def test_execute_runbook_never_raises(monkeypatch):
    sandbox = _FakeSandbox(_success_result())

    def _boom(_path):
        raise RuntimeError("runbook load failed")

    monkeypatch.setattr("app.graph.nodes.load_executable_runbooks", _boom)
    out = _nodes(sandbox).execute_runbook(_state())
    assert out["route"] == "escalate"
    assert out["outcome"] == "escalated"
