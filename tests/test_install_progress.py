"""Unit tests for the live discovery status chart."""
from __future__ import annotations

from io import StringIO

from app.install.models import ToolKind
from app.install.progress import (
    DiscoveryProgress,
    NullDiscoveryProgress,
    make_progress,
)


def test_make_progress_disabled_without_tty():
    stream = StringIO()  # StringIO.isatty() is False
    progress = make_progress("local", stream=stream)
    assert isinstance(progress, NullDiscoveryProgress)
    progress.start()
    progress.phase("docker")
    progress.probing(ToolKind.PROMETHEUS, "http://127.0.0.1:9090")
    progress.result(ToolKind.PROMETHEUS, reachable=True, url="http://127.0.0.1:9090")
    progress.finish(placement="standalone_local")
    assert stream.getvalue() == ""


def test_make_progress_forced_enabled_draws_chart():
    stream = StringIO()
    progress = make_progress("local", enabled=True, stream=stream)
    assert isinstance(progress, DiscoveryProgress)
    progress.start()
    progress.ensure_tools([ToolKind.PROMETHEUS, ToolKind.LOKI])
    progress.phase("docker introspection")
    progress.probing(ToolKind.PROMETHEUS, "http://127.0.0.1:9090")
    progress.result(
        ToolKind.PROMETHEUS,
        reachable=True,
        url="http://127.0.0.1:9090",
        version="2.52.0",
    )
    progress.result(ToolKind.LOKI, reachable=False)
    progress.finish(placement="standalone_local")

    text = stream.getvalue()
    # Final frame (after ANSI noise) should look like the static summary.
    assert "Discovery (1/2 reachable on local)" in text
    assert "prometheus" in text
    assert "http://127.0.0.1:9090" in text
    assert "v2.52.0" in text
    assert "loki" in text
    assert "(not found)" in text
    assert "placement: standalone_local" in text
    assert "Discovery in progress" in text  # intermediate frames also drawn


def test_null_progress_is_safe_noop():
    progress = NullDiscoveryProgress()
    progress.start()
    progress.ensure_tools([ToolKind.GRAFANA])
    progress.found_container(ToolKind.GRAFANA, "grafana")
    progress.probing(ToolKind.GRAFANA, "http://grafana:3000")
    progress.result(ToolKind.GRAFANA, reachable=True, url="http://grafana:3000")
    progress.finish()
    progress.close()


def test_rows_stay_stable_while_probing():
    """During probing, row order must not jump (cursor redraw depends on it)."""
    stream = StringIO()
    progress = DiscoveryProgress(target="local", stream=stream, enabled=True)
    progress.start()
    progress.ensure_tools([ToolKind.PROMETHEUS, ToolKind.LOKI, ToolKind.MAILPIT])
    progress.result(ToolKind.LOKI, reachable=True, url="http://loki:3100")
    mid = progress._lines()
    names = [
        line.split("]", 1)[1].split()[0]
        for line in mid
        if line.lstrip().startswith("[")
    ]
    assert names == ["prometheus", "loki", "mailpit"]
    progress.finish()
    final = progress._lines(placement="standalone_local")
    names = [
        line.split("]", 1)[1].split()[0]
        for line in final
        if line.lstrip().startswith("[")
    ]
    # Finished chart sorts reachable first.
    assert names[0] == "loki"
