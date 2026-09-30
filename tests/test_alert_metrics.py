"""Alert-keyed metrics (metrics_profile.alert_metrics)."""
from __future__ import annotations

from app.dependency_map import DependencyMap
from app.graph.nodes import DiagnosticNodes
from app.profile import get_profile
from app.profile.models import MetricsProfile


class _FakeProm:
    def __init__(self):
        self.queries: list[str] = []

    def instant(self, promql: str):
        self.queries.append(promql)
        if "node_filesystem" in promql:
            return 0.94
        return None


class _FakeLoki:
    def query_range(self, logql, **_kw):
        return []

    def format_log_entries(self, entries):
        return []


def _retrieve(alert_type: str):
    prom = _FakeProm()
    nodes = DiagnosticNodes(prom, _FakeLoki(), None, DependencyMap({"services": {}}), None, None)
    out = nodes.retrieve({"service": "host", "alert_type": alert_type})
    return out, prom


def test_from_dict_parses_alert_metrics():
    profile = MetricsProfile.from_dict(
        {"templates": {"a": "up"}, "alert_metrics": {"HostDiskSpaceLow": ["a"]}}
    )
    assert profile.alert_metrics == {"HostDiskSpaceLow": ("a",)}
    assert "alert_metrics" not in profile.templates


def test_presets_map_disk_alerts_to_disk_templates():
    metrics = get_profile().metrics
    for alert in ("HostDiskSpaceLow", "HostDiskSpaceCritical", "HostDiskFillPredicted"):
        names = metrics.alert_metrics.get(alert)
        assert names and "disk_used_ratio_max" in names
        for name in (n for n in names if n.startswith("disk_")):
            query = metrics.render(name, service="host")
            assert query and "node_filesystem" in query and "{" in query


def test_retrieve_collects_disk_metrics_for_host_disk_alert():
    out, prom = _retrieve("HostDiskFillPredicted")
    host = out["prom_data"]["host"]
    assert host["disk_used_ratio_max"] == 0.94
    assert "disk_avail_predicted_24h_bytes" in host
    assert any("predict_linear" in q for q in prom.queries)


def test_retrieve_skips_disk_metrics_for_other_alerts():
    out, prom = _retrieve("HighErrorRate")
    assert "disk_used_ratio_max" not in out["prom_data"]["host"]
    assert not any("node_filesystem" in q for q in prom.queries)
