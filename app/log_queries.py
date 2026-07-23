"""Build Loki LogQL for diagnostic retrieve.

Alert labels often use *logical* service names (`security`, `postgres`,
`openai`) while the matching lines are emitted by `platform-service` /
`api-gateway`. This module maps those labels to real selectors and, when the
alertname is known, applies the same line filter the Loki ruler uses so the
email sample matches what fired the alert.
"""
from __future__ import annotations

# Line filters mirrored from infrastructure/docker/loki/loki-alert-rules.yml.
# When present we omit the ERROR|WARN level gate so INFO-level matches that
# still trip the ruler are included (auth anomalies often land as WARN/INFO).
ALERT_LINE_FILTERS: dict[str, str] = {
    "SecurityAuthErrorsInLogs": (
        "(?i)(jwt|csrf|access denied|authentication failed|cross-tenant|account locked)"
    ),
    "PostgresErrorsInLogs": (
        "(?i)(postgres|jdbc|hikari|connection).*(refused|timeout|exhaust)"
    ),
    "RedisErrorsInLogs": (
        "(?i)(redis|lettuce).*(timeout|refused|connection)"
    ),
    "ElasticsearchErrorsInLogs": (
        "(?i)(elasticsearch|RestHighLevelClient).*(error|failed|rejected)"
    ),
    "ExternalApiErrorsInLogs": (
        "(?i)(openai|smtp|twilio|s3|amazonaws).*(error|failed|timeout|5[0-9][0-9])"
    ),
    "FrontendJsErrorSpike": (
        "(?i)(error|exception|TypeError|ReferenceError)"
    ),
}


def stream_selector(
    *,
    service: str,
    log_services: list[str] | None = None,
    log_selector: str | None = None,
) -> str:
    """Return a Loki stream selector `{...}` for the alert's service label."""
    if log_selector:
        sel = log_selector.strip()
        if not sel.startswith("{"):
            sel = "{" + sel + "}"
        return sel
    services = [s for s in (log_services or []) if s] or [service]
    if len(services) == 1:
        return f'{{service="{services[0]}"}}'
    joined = "|".join(services)
    return f'{{service=~"{joined}"}}'


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
    selector = stream_selector(
        service=service,
        log_services=log_services,
        log_selector=log_selector,
    )
    alert = (alert_type or "").strip()
    line_filter = ALERT_LINE_FILTERS.get(alert)
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

    logql = f'{selector} | json | level=~"ERROR|WARN"'
    meta = {
        "selector": selector,
        "line_filter": None,
        "level": "ERROR|WARN",
        "service": service,
        "log_services": log_services or [service],
    }
    return logql, meta
