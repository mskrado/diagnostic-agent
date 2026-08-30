"""Candidates, verdicts, and drafted files.

Every line `diag draft` proposes is a :class:`Candidate`: a value, the evidence
that suggested it, and a verdict from the oracle. Verified candidates are
written; the rest are written **commented out with the reason they failed**, so
a draft never contains a silent guess and a reviewer can see what the stack
refused to confirm.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# A live check confirmed the value (a query returned data, a regex matched).
VERIFIED = "verified"
# A live check ran and came back empty. Written commented out.
REJECTED = "rejected"
# No check applies or the inputs were missing (e.g. no sampled lines). Written
# commented out: unconfirmed is not the same as wrong, but it is not proven.
UNVERIFIED = "unverified"


@dataclass(frozen=True)
class Candidate:
    """One proposed value plus its provenance and verdict."""

    key: str
    value: Any
    why: str
    verdict: str = VERIFIED
    detail: str = ""

    @property
    def accepted(self) -> bool:
        return self.verdict == VERIFIED

    def reason(self) -> str:
        """One-line explanation for the report and the commented-out entry."""
        if self.detail:
            return f"{self.verdict}: {self.detail}"
        return self.verdict

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "why": self.why,
            "verdict": self.verdict,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class PresetScore:
    """How much of a preset's metric suite actually returns data here."""

    name: str
    verified: int
    total: int
    probe_service: str
    markers: tuple[str, ...] = ()

    @property
    def ratio(self) -> float:
        return self.verified / self.total if self.total else 0.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "verified": self.verified,
            "total": self.total,
            "ratio": round(self.ratio, 3),
            "probe_service": self.probe_service,
            "markers": list(self.markers),
        }


@dataclass(frozen=True)
class DraftedFile:
    """One file to write, with the candidates behind it."""

    path: str
    content: str
    candidates: tuple[Candidate, ...] = ()

    @property
    def accepted(self) -> tuple[Candidate, ...]:
        return tuple(c for c in self.candidates if c.accepted)

    @property
    def withheld(self) -> tuple[Candidate, ...]:
        return tuple(c for c in self.candidates if not c.accepted)


@dataclass(frozen=True)
class CopiedFile:
    """A reference runbook carried into the draft verbatim."""

    path: str
    source: str
    reason: str


@dataclass(frozen=True)
class DraftResult:
    files: tuple[DraftedFile, ...] = ()
    copied: tuple[CopiedFile, ...] = ()
    preset: str = ""
    preset_scores: tuple[PresetScore, ...] = ()
    # Alert names with no runbook in the reference corpus (Phase 3 territory).
    uncovered_alerts: tuple[str, ...] = ()
    # Reference runbooks about alerts this stack does not have.
    unused_runbooks: tuple[str, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def all_candidates(self) -> tuple[Candidate, ...]:
        out: list[Candidate] = []
        for drafted in self.files:
            out.extend(drafted.candidates)
        return tuple(out)

    def to_dict(self) -> dict:
        return {
            "preset": self.preset,
            "preset_scores": [s.to_dict() for s in self.preset_scores],
            "files": [
                {
                    "path": f.path,
                    "candidates": [c.to_dict() for c in f.candidates],
                }
                for f in self.files
            ],
            "copied": [
                {"path": c.path, "source": c.source, "reason": c.reason}
                for c in self.copied
            ],
            "uncovered_alerts": list(self.uncovered_alerts),
            "unused_runbooks": list(self.unused_runbooks),
            "warnings": list(self.warnings),
        }
