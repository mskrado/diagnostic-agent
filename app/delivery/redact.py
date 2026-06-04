"""Tenant-safety redaction.

publishi.ai is multi-tenant; diagnostic output must not leak tenant-identifying
data into shared destinations (audit logs viewed across tenants, Grafana
annotations, future Slack). This scrubs obvious tenant identifiers from the
free-text surfaces of a report while leaving metrics/structure intact.
"""
from __future__ import annotations

import re

# tenantId values look like "tenant-42", UUIDs, or appear as "tenantId":"..."
_TENANT_KV = re.compile(r'("?tenant[_-]?id"?\s*[:=]\s*")[^"]*(")', re.IGNORECASE)
_TENANT_TOKEN = re.compile(r"\btenant-[0-9a-zA-Z]+\b", re.IGNORECASE)
_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE
)


def redact_text(text: str) -> str:
    text = _TENANT_KV.sub(r"\1[REDACTED]\2", text)
    text = _TENANT_TOKEN.sub("tenant-[REDACTED]", text)
    text = _UUID.sub("[UUID-REDACTED]", text)
    return text


def redact_log_lines(lines: list[str]) -> list[str]:
    return [redact_text(line) for line in lines]
