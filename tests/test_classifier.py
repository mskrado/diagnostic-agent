from app import config as config_mod
from app.execution.classifier import classify
from app.profile.models import AllowlistedAction


def _action(**kw) -> AllowlistedAction:
    base = dict(
        id="clear-cdn-cache",
        description="Purge the CDN edge cache",
        argv=("cache-purge", "--service", "{service}"),
        params=(),
        scope_services=("web-gateway",),
        destructive=False,
        timeout_s=60,
    )
    base.update(kw)
    return AllowlistedAction(**base)


def test_profile_destructive_flag_forces_hold():
    verdict = classify(_action(destructive=True))
    assert verdict.decision == "hold"
    assert verdict.destructive is True


def test_restart_verb_in_argv_holds():
    verdict = classify(
        _action(
            id="restart-worker-pool",
            description="Rolling restart",
            argv=("scale", "restart", "--pool", "{pool}"),
        )
    )
    assert verdict.decision == "hold"
    assert any("restart" in pattern for pattern in verdict.matched_patterns)


def test_non_destructive_action_allows():
    verdict = classify(
        _action(
            id="warm-cache",
            description="Warm the cache",
            argv=("cache-warm", "--service", "{service}"),
        )
    )
    assert verdict.decision == "allow"
    assert verdict.destructive is False


def test_extra_pattern_from_config_holds(monkeypatch):
    monkeypatch.setenv("AGENT_EXEC_DESTRUCTIVE_PATTERNS", "evict")
    config_mod.settings = config_mod.Settings()
    verdict = classify(
        _action(
            id="evict-tenant",
            description="Evict tenant sessions",
            argv=("evict", "--tenant", "{tenant}"),
        )
    )
    assert verdict.decision == "hold"
