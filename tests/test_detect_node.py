from app.dependency_map import DependencyMap
from app.graph.nodes import DiagnosticNodes

_MAP = {
    "services": {
        "platform-service": {"kind": "monolith", "downstream": ["postgres"]},
    }
}


def _nodes():
    dm = DependencyMap(_MAP)
    # detect() only uses the dependency map; other collaborators unused here.
    return DiagnosticNodes(None, None, None, dm, None, None)


def test_detect_resolves_job_label_with_port():
    nodes = _nodes()
    state = {
        "raw_labels": {
            "job": "platform-service:8080",
            "alertname": "HighErrorRate",
            "severity": "warning",
        }
    }
    out = nodes.detect(state)
    assert out["service"] == "platform-service"
    assert out["alert_type"] == "HighErrorRate"
    assert out["severity"] == "warning"


def test_detect_prefers_service_label():
    nodes = _nodes()
    out = nodes.detect({"raw_labels": {"service": "platform-service"}})
    assert out["service"] == "platform-service"
