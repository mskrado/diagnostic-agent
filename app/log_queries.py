"""Build Loki LogQL for diagnostic retrieve — driven by logs_profile.yaml.

Alert labels often use *logical* service names while matching lines are emitted
by other processes. ``service_map.yaml`` maps those labels to real selectors;
the logs profile supplies the label name, level filter, and optional per-alert
line filters.
"""
from __future__ import annotations

from .profile import get_profile


def stream_selector(
    *,
    service: str,
    log_services: list[str] | None = None,
    log_selector: str | None = None,
) -> str:
    """Return a Loki stream selector ``{...}`` for the alert's service label."""
    if log_selector:
        sel = log_selector.strip()
        if not sel.startswith("{"):
            sel = "{" + sel + "}"
        return sel
    label = get_profile().logs.service_label
    services = [s for s in (log_services or []) if s] or [service]
    if len(services) == 1:
        return f'{{{label}="{services[0]}"}}'
    joined = "|".join(services)
    return f'{{{label}=~"{joined}"}}'


def build_retrieve_logql(
    *,
    service: str,
    alert_type: str | None = None,
    log_services: list[str] | None = None,
    log_selector: str | None = None,
) -> tuple[str, dict]:
    """Return (logql, metadata) for the retrieve node / email log_source block.

    metadata includes selector, optional line_filter, and level filter used.
    """
    logs = get_profile().logs
    selector = stream_selector(
        service=service,
        log_services=log_services,
        log_selector=log_selector,
    )
    alert = (alert_type or "").strip()
    line_filter = logs.alert_line_filters.get(alert)
    if line_filter:
        logql = f'{selector} |~ "{line_filter}"'
        meta = {
            "selector": selector,
            "line_filter": line_filter,
            "level": None,
            "service": service,
            "log_services": log_services or [service],
        }
        return logql, meta

    if logs.use_json_parser:
        logql = f'{selector} | json | level=~"{logs.level_filter}"'
    else:
        logql = f'{selector} | level=~"{logs.level_filter}"'
    meta = {
        "selector": selector,
        "line_filter": None,
        "level": logs.level_filter,
        "service": service,
        "log_services": log_services or [service],
    }
    return logql, meta


# Back-compat for tests / importers that still reference the module constant.
def __getattr__(name: str):
    if name == "ALERT_LINE_FILTERS":
        return dict(get_profile().logs.alert_line_filters)
    raise AttributeError(name)
