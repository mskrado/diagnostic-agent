"""Human-readable drift report."""
from __future__ import annotations

from .models import DriftReport


def render(report: DriftReport) -> str:
    rule = "-" * 72
    lines = [
        "diag drift",
        f"workspace={report.workspace or '(none)'}",
        f"evidence_at={report.evidence_at or '(unknown)'}",
        f"status={'OK' if report.ok else 'DRIFT'}",
    ]

    if report.errors:
        lines.extend(["", "errors (fail the gate)", rule])
        for item in report.errors:
            label = f"{item.kind}:{item.name}" if item.name else item.kind
            lines.append(f"  {label}")
            lines.append(f"    {item.detail}")

    if report.notes:
        lines.extend(["", "notes (informational)", rule])
        for item in report.notes:
            label = f"{item.kind}:{item.name}" if item.name else item.kind
            lines.append(f"  {label}")
            lines.append(f"    {item.detail}")

    if report.warnings:
        lines.extend(["", "warnings", rule])
        for warning in report.warnings:
            lines.append(f"  - {warning}")

    if report.ok and not report.notes and not report.warnings:
        lines.extend(["", "no drift detected"])

    return "\n".join(lines)
