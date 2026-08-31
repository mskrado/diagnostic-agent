"""Workspace drift findings.

Error-class drift fails ``diag drift`` (exit 1). Notes are informational and
do not fail the gate — unused runbooks are a cleanup hint, not a broken agent.
"""
from __future__ import annotations

from dataclasses import dataclass, field

ERROR = "error"
NOTE = "note"


@dataclass(frozen=True)
class DriftItem:
    kind: str
    severity: str  # ERROR | NOTE
    detail: str
    name: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "name": self.name,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class DriftReport:
    items: tuple[DriftItem, ...] = ()
    workspace: str = ""
    evidence_at: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def errors(self) -> tuple[DriftItem, ...]:
        return tuple(i for i in self.items if i.severity == ERROR)

    @property
    def notes(self) -> tuple[DriftItem, ...]:
        return tuple(i for i in self.items if i.severity == NOTE)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "workspace": self.workspace,
            "evidence_at": self.evidence_at,
            "errors": [i.to_dict() for i in self.errors],
            "notes": [i.to_dict() for i in self.notes],
            "warnings": list(self.warnings),
        }
