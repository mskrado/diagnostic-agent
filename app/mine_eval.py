"""Mine blind-eval cases from redacted audit JSONL.

Audit records already went through the workspace redaction pipeline when they
were written. This module re-scrubs with the built-in patterns as a belt-and-
braces step, then drafts ``blind_eval`` cases an operator can curate.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from .scan.scrub import scrub_text

logger = logging.getLogger(__name__)

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
_WEAK = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "error",
        "exception",
        "failed",
        "failure",
        "service",
        "level",
        "message",
        "logger",
        "timestamp",
        "trace",
        "null",
        "true",
        "false",
        "http",
        "https",
        "java",
        "org",
        "com",
        "spring",
        "nested",
    }
)


@dataclass(frozen=True)
class MinedCase:
    case: dict
    source: str
    fingerprint: str


@dataclass
class MineResult:
    cases: list[MinedCase] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def case_dicts(self) -> list[dict]:
        return [m.case for m in self.cases]


def load_audit_records(paths: Iterable[Path]) -> list[tuple[str, dict]]:
    """Load JSONL audit records from files and directories."""
    records: list[tuple[str, dict]] = []
    for path in paths:
        path = Path(path)
        if path.is_dir():
            files = sorted(path.glob("diagnostics-*.jsonl")) + sorted(
                path.glob("*.jsonl")
            )
            # De-dupe while preserving order.
            seen: set[Path] = set()
            ordered: list[Path] = []
            for f in files:
                if f not in seen:
                    seen.add(f)
                    ordered.append(f)
            for f in ordered:
                records.extend(_read_jsonl(f))
        elif path.is_file():
            records.extend(_read_jsonl(path))
        else:
            logger.warning("audit path does not exist: %s", path)
    return records


def _read_jsonl(path: Path) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("cannot read %s: %s", path, exc)
        return out
    for i, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            logger.warning("%s:%s: %s", path, i, exc)
            continue
        if isinstance(data, dict):
            out.append((f"{path.name}:{i}", data))
    return out


def mine_records(
    records: list[tuple[str, dict]],
    *,
    min_logs: int = 2,
    max_cases: int = 50,
) -> MineResult:
    """Turn audit records into draft blind-eval cases."""
    result = MineResult()
    seen: set[str] = set()
    for source, record in records:
        if len(result.cases) >= max_cases:
            result.warnings.append(f"stopped at max_cases={max_cases}")
            break
        report = record.get("report")
        if not isinstance(report, dict):
            # Some writers nest under the record itself.
            report = record if "alert_type" in record or "evidence" in record else None
        if not isinstance(report, dict):
            result.skipped.append(f"{source}: no report object")
            continue

        logs = _extract_logs(report)
        logs = [scrub_text(line) for line in logs if isinstance(line, str) and line.strip()]
        if len(logs) < min_logs:
            result.skipped.append(f"{source}: fewer than {min_logs} log lines")
            continue

        diagnosis = report.get("diagnosis")
        if not isinstance(diagnosis, dict) or not diagnosis:
            result.skipped.append(f"{source}: no diagnosis")
            continue

        alertname = str(
            report.get("alert_type")
            or (report.get("alert") or {}).get("alertname")
            or "UnknownAlert"
        )
        service = str(report.get("service") or "unknown")
        severity = str(report.get("severity") or "warning")
        root = _root_cause(diagnosis)
        if not root:
            result.skipped.append(f"{source}: empty root cause")
            continue

        fingerprint = _fingerprint(alertname, logs)
        if fingerprint in seen:
            result.skipped.append(f"{source}: duplicate of earlier case")
            continue
        seen.add(fingerprint)

        must_ref = _must_reference(logs)
        keywords = _cause_keywords(alertname, service, root)
        metrics = _extract_metrics(report)
        case_id = _case_id(alertname, service, fingerprint)

        case = {
            "id": case_id,
            "system": _system_hint(alertname, service),
            "alert": {
                "alertname": alertname,
                "service": service,
                "severity": severity,
            },
            "metrics": metrics,
            "logs": logs[:12],
            "expected": {
                "cause_keywords": keywords,
                "must_reference": must_ref,
                "root_cause": root,
            },
            "mined_from": source,
        }
        result.cases.append(
            MinedCase(case=case, source=source, fingerprint=fingerprint)
        )
    return result


def render_dataset(result: MineResult) -> str:
    """YAML document ready to write as a draft blind_eval file."""
    header = """\
# Draft blind-eval cases mined from audit logs by `diag mine-eval`.
#
# WHAT THIS FILE DOES
#   Candidate cases for blind_eval.yaml. Each case's logs were re-scrubbed
#   with the built-in secret patterns. Review, edit root_cause / keywords,
#   then merge into your curated blind_eval.yaml.
#
# SAFETY
#   Do not commit this file until you have read every log line. Prefer
#   renaming to blind_eval.yaml only after curation.
#
"""
    body = {
        "version": 1,
        "cases": result.case_dicts,
    }
    dumped = yaml.safe_dump(body, sort_keys=False, width=100, allow_unicode=True)
    return header + dumped


def mine_paths(
    paths: Iterable[Path],
    *,
    min_logs: int = 2,
    max_cases: int = 50,
) -> MineResult:
    return mine_records(
        load_audit_records(paths), min_logs=min_logs, max_cases=max_cases
    )


# -- helpers -----------------------------------------------------------------
def _extract_logs(report: dict) -> list[str]:
    evidence = report.get("evidence") or {}
    raw = evidence.get("error_log_sample") or report.get("logs") or []
    if isinstance(raw, list):
        out: list[str] = []
        for item in raw:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                out.append(str(item[1]))
            elif isinstance(item, dict) and item.get("line"):
                out.append(str(item["line"]))
        return out
    return []


def _extract_metrics(report: dict) -> dict:
    evidence = report.get("evidence") or {}
    metrics = evidence.get("metrics")
    return metrics if isinstance(metrics, dict) else {}


def _root_cause(diagnosis: dict) -> str:
    primary = diagnosis.get("primary_hypothesis")
    if isinstance(primary, dict):
        for key in ("cause", "hypothesis", "summary", "text"):
            if primary.get(key):
                return str(primary[key]).strip()
    if isinstance(primary, str) and primary.strip():
        return primary.strip()
    categories = diagnosis.get("issue_categories") or []
    if categories and isinstance(categories[0], dict):
        cause = categories[0].get("cause") or categories[0].get("category")
        if cause:
            return str(cause).strip()
    return ""


def _fingerprint(alertname: str, logs: list[str]) -> str:
    blob = alertname + "\n" + "\n".join(logs[:5])
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def _case_id(alertname: str, service: str, fingerprint: str) -> str:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", alertname)
    spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "-", spaced)
    slug = re.sub(r"[^a-z0-9]+", "-", spaced.lower()).strip("-") or "case"
    svc = re.sub(r"[^a-z0-9]+", "-", service.lower()).strip("-") or "svc"
    return f"mined-{slug}-{svc}-{fingerprint[:6]}"


def _must_reference(logs: list[str], *, limit: int = 4) -> list[str]:
    """Pick distinctive tokens that appear in the logs (lint grounding)."""
    joined = "\n".join(logs).lower()
    counts: dict[str, int] = {}
    for token in _TOKEN.findall(joined):
        if token in _WEAK or len(token) < 4:
            continue
        counts[token] = counts.get(token, 0) + 1
    # Prefer tokens that appear at least once but are not ubiquitous.
    ranked = sorted(counts.items(), key=lambda kv: (kv[1], -len(kv[0])), reverse=True)
    picked: list[str] = []
    for token, _count in ranked:
        if token not in picked:
            picked.append(token)
        if len(picked) >= limit:
            break
    # Also keep any quoted port-like numbers that are useful grounding.
    for match in re.findall(r"\b(\d{4,5})\b", joined):
        if match not in picked and len(picked) < limit + 1:
            picked.append(match)
    return picked[:limit] or ["error"]


def _cause_keywords(alertname: str, service: str, root: str) -> list[str]:
    parts = re.findall(r"[A-Za-z]{3,}", f"{alertname} {service} {root}")
    out: list[str] = []
    for part in parts:
        low = part.lower()
        if low in _WEAK or low in out:
            continue
        out.append(low)
        if len(out) >= 5:
            break
    return out or [service.lower()]


def _system_hint(alertname: str, service: str) -> str:
    lower = f"{alertname} {service}".lower()
    for hint in (
        "postgres",
        "redis",
        "elasticsearch",
        "kafka",
        "smtp",
        "s3",
        "openai",
        "twilio",
        "jvm",
        "gateway",
        "disk",
        "container",
    ):
        if hint in lower:
            return hint
    return service or "unknown"
