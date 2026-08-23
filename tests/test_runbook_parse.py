from app.execution.runbook import (
    ExecutableRunbook,
    RunbookStep,
    parse_runbook_actions,
    select_runbook,
)

PROSE_ONLY = "# Runbook\n\n## Symptoms\n- things break\n"

WITH_BLOCK = (
    "# Runbook\n\n## Automated actions\n"
    "```runbook-actions\n"
    "version: 1\n"
    "match:\n"
    "  alert_type: [HighErrorRate]\n"
    "  service: [web-gateway]\n"
    "  min_confidence: high\n"
    "steps:\n"
    "  - action_id: clear-cdn-cache\n"
    "```\n"
)


def test_prose_only_runbook_returns_none():
    assert parse_runbook_actions(PROSE_ONLY) is None


def test_block_parsed():
    runbook = parse_runbook_actions(WITH_BLOCK, path="rb.md")
    assert runbook is not None
    assert runbook.alert_types == ["HighErrorRate"]
    assert runbook.services == ["web-gateway"]
    assert runbook.min_confidence == "high"
    assert runbook.steps[0].action_id == "clear-cdn-cache"


def _rb(**kw):
    base = dict(
        path="rb.md",
        alert_types=["HighErrorRate"],
        services=["web-gateway"],
        min_confidence="high",
        steps=[RunbookStep("clear-cdn-cache")],
    )
    base.update(kw)
    return ExecutableRunbook(**base)


def test_select_single_match():
    runbook = select_runbook(
        [_rb()],
        alert_type="HighErrorRate",
        service="web-gateway",
        confidence_note="high",
    )
    assert runbook is not None


def test_select_low_confidence_no_match():
    runbook = select_runbook(
        [_rb()],
        alert_type="HighErrorRate",
        service="web-gateway",
        confidence_note="low",
    )
    assert runbook is None


def test_select_ambiguous_returns_none():
    runbook = select_runbook(
        [_rb(), _rb(path="other.md")],
        alert_type="HighErrorRate",
        service="web-gateway",
        confidence_note="high",
    )
    assert runbook is None
