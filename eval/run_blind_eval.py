"""Diagnostic eval for the diagnostic-agent LLM (RAG-off and RAG-on).

Default (blind / RAG-off)
    Measure how well the LLM identifies a root cause from LOGS ALONE, with
    runbook context forced to "none". Isolates the model from the local KB.

With --rag / EVAL_INCLUDE_RAG=true (RAG-on)
    Same prompts and scoring, but retrieve runbook chunks exactly like the live
    agent's rag_lookup node and inject them into
    "Runbook / past-incident context: ...". Use this to quantify how much RAG
    helps (compare two result JSON files).

It reuses the real system prompt, model factory, output schema, and Loki log
formatting. Offline mode needs LLM (+ embeddings when --rag). Live mode POSTs
to a running agent (--live-url); RAG then depends on the agent's
AGENT_RAG_ENABLED.

Usage
-----
    cd diagnostic-agent
    # blind (default)
    python eval/run_blind_eval.py --only jvm-heap-oom
    # with RAG (offline)
    python eval/run_blind_eval.py --rag --only jvm-heap-oom --judge
    # or set EVAL_INCLUDE_RAG=true in diagnostic-agent/.env
    # live (agent's RAG setting applies)
    python eval/run_blind_eval.py --live-url http://localhost:8001 --loki-url http://localhost:3100
    # mixed errors in one request (realistic Loki sample)
    python eval/run_blind_eval.py --merge --only postgres-connectivity,redis-connection,jvm-heap-oom
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

_DATASET = Path(__file__).resolve().parent / "blind_eval_dataset.yaml"
_RESULTS_DIR = Path(__file__).resolve().parent / "results"

# Cached RAG store for offline --rag runs (built once per process).
_RAG_STORE = None


def _load_eval_dotenv() -> None:
    """Load diagnostic-agent/.env into os.environ (EVAL_INCLUDE_RAG is not AGENT_*).

    pydantic-settings only applies matching AGENT_* fields; EVAL_* must be loaded
    explicitly for os.environ.get() in main().
    """
    env_path = _PKG_ROOT / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
    except ImportError:
        # Minimal fallback: KEY=VALUE lines (no export / interpolation)
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


# --------------------------------------------------------------------------
# dataset
# --------------------------------------------------------------------------
def load_cases(path: Path) -> list[dict]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = raw.get("cases") or []
    if not cases:
        raise ValueError(f"No cases in {path}")
    return cases


def merge_cases(cases: list[dict], *, seed: int = 42) -> dict:
    """Combine multiple cases into one mixed-error request (realistic Loki sample).

    Logs are round-robin interleaved then shuffled with ``seed`` so concurrent
    failure types appear together. Metrics are deep-merged by service key.
    Alert labels use a generic HighErrorRate (production rarely fires a
    single-symptom alert when several domains are noisy).
    """
    if len(cases) < 2:
        raise ValueError("--merge requires at least 2 cases (use --only to pick them)")

    queues = [list(c.get("logs") or []) for c in cases]
    interleaved: list[str] = []
    while any(queues):
        for q in queues:
            if q:
                interleaved.append(q.pop(0))
    rng = random.Random(seed)
    rng.shuffle(interleaved)

    metrics: dict = {}
    for c in cases:
        for svc, vals in (c.get("metrics") or {}).items():
            bucket = metrics.setdefault(svc, {})
            if isinstance(vals, dict):
                bucket.update(vals)

    services = {
        (c.get("alert") or {}).get("service") or "platform-service" for c in cases
    }
    service = next(iter(services)) if len(services) == 1 else "platform-service"
    severities = {(c.get("alert") or {}).get("severity") or "warning" for c in cases}
    severity = "critical" if "critical" in severities else "warning"

    ids = [c["id"] for c in cases]
    return {
        "id": "merged-" + "+".join(ids),
        "system": "mixed",
        "merged_from": ids,
        "source_cases": cases,
        "alert": {
            "alertname": "HighErrorRate",
            "service": service,
            "severity": severity,
        },
        "metrics": metrics,
        "logs": interleaved,
        "dependencies": [],
        "blast_radius": [],
        "expected": {
            "systems": [c.get("system") for c in cases],
            "root_cause": "Concurrent failures: "
            + "; ".join(
                f"{c.get('system')}: {(c.get('expected') or {}).get('root_cause', '')}"
                for c in cases
            ),
        },
    }


def format_logs(lines: list[str]) -> list[str]:
    """Format raw JSON log lines exactly like the live retrieve node does."""
    from app.clients.loki import LokiClient

    # (ts_ns, line) pairs; @timestamp inside the JSON overrides the dummy ts.
    pairs = [(str(1_000_000_000 + i), line) for i, line in enumerate(lines)]
    return LokiClient.format_log_entries(pairs)


def rag_queries_for_case(case: dict, logs: list[str]) -> list[str]:
    """Same multi-family RAG queries as DiagnosticNodes.rag_lookup."""
    from app.rag.queries import build_rag_queries

    alert = case.get("alert", {})
    return build_rag_queries(
        alert_type=alert.get("alertname", "") or "",
        service=alert.get("service", "") or "",
        module_hint=alert.get("module", "") or "",
        log_lines=logs,
    )


def get_rag_store():
    """Build (once) the same Chroma store the live agent uses at startup."""
    global _RAG_STORE
    if _RAG_STORE is None:
        from app.rag.store import build_rag_store

        _RAG_STORE = build_rag_store()
    return _RAG_STORE


def retrieve_rag_context(case: dict, logs: list[str]) -> str:
    store = get_rag_store()
    if not store.available:
        return ""
    return store.query_many(rag_queries_for_case(case, logs)) or ""


def build_prompt(case: dict, rag_context: str = "") -> tuple[str, str]:
    """Return (system_prompt, human_content) mirroring nodes.correlate.

    rag_context empty → 'none' (blind). Non-empty → injected runbook chunks.
    Mixed (--merge) cases raise the log sample to 20 so more failure types fit
    (live retrieve keeps up to 20 formatted lines; correlate still samples 10
    in production — we allow a wider window for multi-cause eval).
    """
    from app.graph.prompts import SYSTEM_PROMPT

    alert = case.get("alert", {})
    logs = format_logs(case.get("logs", []))
    sample_n = 20 if case.get("merged_from") else 10
    rag_slot = rag_context.strip() if rag_context and rag_context.strip() else "none"
    human = (
        f"Alert: {alert.get('alertname')} on {alert.get('service')} "
        f"(severity: {alert.get('severity')})\n"
        f"Suspected module: {alert.get('module') or 'unknown'}\n"
        f"Dependencies checked: {case.get('dependencies', [])}\n"
        f"Metrics snapshot: {json.dumps(case.get('metrics', {}))}\n"
        f"Recent error/warn logs (sample): {logs[:sample_n]}\n"
        f"Runbook / past-incident context: {rag_slot}\n"
        f"Downstream services at risk: {case.get('blast_radius', [])}"
    )
    return SYSTEM_PROMPT, human


# --------------------------------------------------------------------------
# model runners
# --------------------------------------------------------------------------
def make_model():
    from app.llm import get_structured_diagnosis_llm

    return get_structured_diagnosis_llm()


def run_offline(model, case: dict, *, include_rag: bool = False) -> dict:
    """Return {diagnosis, llm_exchange} for offline scoring + prompt/token audit."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.config import settings
    from app.llm import content_to_text, invoke_structured_diagnosis
    from app.llm_usage import extract_token_usage

    formatted = format_logs(case.get("logs", []))
    rag_context = retrieve_rag_context(case, formatted) if include_rag else ""
    system, human = build_prompt(case, rag_context=rag_context)
    result = invoke_structured_diagnosis(
        model, [SystemMessage(content=system), HumanMessage(content=human)]
    )
    parsed = result.get("parsed") if isinstance(result, dict) else None
    raw_msg = result.get("raw") if isinstance(result, dict) else None
    raw = content_to_text(getattr(raw_msg, "content", ""))
    token_usage = extract_token_usage(raw_msg)
    exchange = {
        "system_prompt": system,
        "user_prompt": human,
        "rag_context": rag_context,
        "rag_used": bool(rag_context),
        "token_usage": token_usage,
        "llm_raw": raw,
        **settings.model_snapshot(),
    }
    if parsed is not None:
        return {"diagnosis": parsed.model_dump(), "llm_exchange": exchange}
    return {
        "diagnosis": {
            "error": "LLM did not return valid structured output",
            "raw": raw,
        },
        "llm_exchange": exchange,
    }


def _stamp_log_line(line: str, when_iso: str) -> str:
    """Refresh @timestamp inside a Spring Boot JSON log line to `when_iso`."""
    try:
        doc = json.loads(line)
        if isinstance(doc, dict):
            doc["@timestamp"] = when_iso
            return json.dumps(doc, separators=(",", ":"))
    except (json.JSONDecodeError, TypeError):
        pass
    return line


def push_logs_to_loki(
    loki_url: str, case: dict, *, repeats: int = 15
) -> None:
    """Push the case's log lines into Loki under {service=...}.

    Real platform-service traffic (e.g. S3/SMTP health checks) shares the same
    Loki stream. The agent samples the newest ERROR|WARN lines, so we stamp
    lines to *now* and repeat them enough times that they dominate that window.
    """
    import httpx

    svc = case.get("alert", {}).get("service", "platform-service")
    raw_lines = case.get("logs") or []
    if not raw_lines:
        return

    now = datetime.now(timezone.utc)
    now_ns = int(now.timestamp() * 1e9)
    values: list[list[str]] = []
    i = 0
    # Newest last; repeat so the agent's [:20] sample is mostly our case.
    for _ in range(max(1, repeats)):
        for line in raw_lines:
            when = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
            stamped = _stamp_log_line(line, when)
            values.append([str(now_ns + i * 1_000_000), stamped])
            i += 1

    body = {
        "streams": [
            {"stream": {"service": svc, "level": "ERROR", "blind_eval": case["id"]}, "values": values}
        ]
    }
    with httpx.Client(timeout=10.0) as client:
        r = client.post(f"{loki_url.rstrip('/')}/loki/api/v1/push", json=body)
        r.raise_for_status()


def run_live(live_url: str, case: dict, loki_url: str | None) -> dict:
    """Return {diagnosis, llm_exchange} from a running agent's /alert response."""
    import httpx

    if loki_url:
        # Push twice: once to ingest, again immediately before /alert so case
        # lines are the newest in Loki (ahead of ongoing health-check spam).
        push_logs_to_loki(loki_url, case)
        time.sleep(2)
        push_logs_to_loki(loki_url, case)
        time.sleep(1)

    payload = {
        "alerts": [
            {
                "status": "firing",
                "labels": case.get("alert", {}),
                "annotations": {
                    "summary": f"blind-eval {case['id']}",
                    "description": (
                        f"Synthetic blind-eval case {case['id']}; "
                        "prefer injected Loki lines over ambient health-check noise."
                    ),
                },
            }
        ]
    }
    with httpx.Client(timeout=float(300)) as client:
        r = client.post(f"{live_url.rstrip('/')}/alert", json=payload)
        r.raise_for_status()
        reports = r.json().get("reports", [])
    if not reports:
        return {
            "diagnosis": {"error": "no report returned"},
            "llm_exchange": {},
        }
    report = reports[0]
    diag = report.get("diagnosis", {})
    exchange = report.get("llm_exchange") or {}
    if "_rag_used" not in diag:
        diag = {**diag, "_rag_used": report.get("evidence", {}).get("rag_used")}
    return {"diagnosis": diag, "llm_exchange": exchange}


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------
def _text_pool(diag: dict) -> str:
    primary = diag.get("primary_hypothesis", {}) or {}
    parts = [primary.get("cause", ""), primary.get("evidence", "")]
    for h in diag.get("secondary_hypotheses", []) or []:
        parts.append(h.get("cause", ""))
    # Per-category assessments (multi-issue diagnosis) also count toward recall.
    for c in diag.get("issue_categories", []) or []:
        if isinstance(c, dict):
            parts.append(c.get("category", ""))
            parts.append(c.get("cause", ""))
            parts.append(c.get("evidence", ""))
            parts.append(c.get("suggested_next_step", ""))
            parts.extend(c.get("tool_run_examples", []) or [])
            parts.extend(c.get("fix_suggestions", []) or [])
    parts.append(diag.get("blast_radius_assessment", ""))
    parts.extend(diag.get("suggested_next_steps", []) or [])
    parts.extend(diag.get("tool_run_examples", []) or [])
    parts.extend(diag.get("fix_suggestions", []) or [])
    return " ".join(parts).lower()


def score_case(case: dict, diag: dict) -> dict:
    expected = case.get("expected", {})
    cause_kw = [k.lower() for k in expected.get("cause_keywords", [])]
    must_ref = [k.lower() for k in expected.get("must_reference", [])]

    primary = diag.get("primary_hypothesis", {}) or {}
    primary_text = f"{primary.get('cause', '')} {primary.get('evidence', '')}".lower()
    evidence = (primary.get("evidence", "") or "").lower()
    pool = _text_pool(diag)

    identified = any(k in primary_text for k in cause_kw) if cause_kw else False
    matched = [k for k in cause_kw if k in pool]
    recall = round(len(matched) / len(cause_kw), 2) if cause_kw else 0.0
    grounded = (any(k in evidence for k in must_ref)) if must_ref else None

    note = diag.get("confidence_note")
    # Special handling for the "insufficient data" control: correct == low confidence
    if case.get("system") == "insufficient-data":
        identified = identified or (note == "low")

    return {
        "identified": identified,
        "keyword_recall": recall,
        "matched_keywords": matched,
        "grounded": grounded,
        "confidence": primary.get("confidence"),
        "confidence_note": note,
        "error": diag.get("error"),
    }


def score_merged_case(merged: dict, diag: dict) -> dict:
    """Score a mixed request against each source case's expected cause.

    A source system counts as a hit when its cause_keywords appear anywhere in
    the diagnosis text pool (primary + secondary + next steps), not only the
    primary hypothesis — mixed incidents should surface concurrent causes.
    """
    sources = merged.get("source_cases") or []
    per: list[dict] = []
    for src in sources:
        expected = src.get("expected") or {}
        cause_kw = [k.lower() for k in expected.get("cause_keywords", [])]
        pool = _text_pool(diag)
        primary = diag.get("primary_hypothesis", {}) or {}
        primary_text = (
            f"{primary.get('cause', '')} {primary.get('evidence', '')}".lower()
        )
        matched = [k for k in cause_kw if k in pool]
        hit = bool(matched) if cause_kw else False
        in_primary = any(k in primary_text for k in cause_kw) if cause_kw else False
        recall = round(len(matched) / len(cause_kw), 2) if cause_kw else 0.0
        per.append(
            {
                "id": src["id"],
                "system": src.get("system"),
                "hit": hit,
                "in_primary": in_primary,
                "keyword_recall": recall,
                "matched_keywords": matched,
            }
        )

    n = len(per) or 1
    hits = sum(1 for p in per if p["hit"])
    primary_hits = sum(1 for p in per if p["in_primary"])
    note = diag.get("confidence_note")
    primary = diag.get("primary_hypothesis", {}) or {}
    return {
        "identified": hits == len(per),
        "systems_hit": hits,
        "systems_total": len(per),
        "systems_hit_rate": round(hits / n, 3),
        "primary_covers_systems": primary_hits,
        "keyword_recall": round(sum(p["keyword_recall"] for p in per) / n, 2),
        "matched_keywords": sorted(
            {k for p in per for k in p["matched_keywords"]}
        ),
        "per_system": per,
        "grounded": None,
        "confidence": primary.get("confidence"),
        "confidence_note": note,
        "error": diag.get("error"),
        "merged_from": list(merged.get("merged_from") or []),
    }


def _diagnosis_payload_for_judge(diag: dict) -> dict:
    """Full diagnosis JSON for the judge (drop internal eval-only keys)."""
    return {
        k: v
        for k, v in (diag or {}).items()
        if not str(k).startswith("_")
    }


def _known_causes_checklist(case: dict) -> tuple[str, list[str]]:
    """Structured known-cause list; skip insufficient-data controls."""
    sources = case.get("source_cases") or []
    if sources:
        lines: list[str] = []
        required_ids: list[str] = []
        controls: list[str] = []
        for src in sources:
            sid = src.get("id") or "?"
            system = src.get("system") or "?"
            root = (src.get("expected") or {}).get("root_cause", "")
            if system == "insufficient-data":
                controls.append(f"- id={sid} (CONTROL): {root}")
                continue
            required_ids.append(sid)
            lines.append(f"- id={sid} system={system}: {root}")
        body = "Required concurrent root causes (credit if found anywhere in MODEL diagnosis):\n"
        body += "\n".join(lines) if lines else "(none)"
        if controls:
            body += (
                "\n\nControl cases (do NOT require a matching hypothesis; "
                "penalize only if the model invents a confident cause with no evidence):\n"
            )
            body += "\n".join(controls)
        return body, required_ids

    root = (case.get("expected") or {}).get("root_cause", "")
    return f"KNOWN root cause:\n{root}", []


def build_judge_prompt(case: dict, diag: dict) -> str:
    """Prompt that grades the FULL model diagnosis, not only primary/secondary."""
    known_block, _required = _known_causes_checklist(case)
    model_json = json.dumps(_diagnosis_payload_for_judge(diag), indent=2, default=str)
    if case.get("merged_from"):
        return (
            "You are grading a diagnostic model on a MIXED multi-failure incident.\n"
            "You MUST review the ENTIRE MODEL diagnosis JSON below — including "
            "issue_categories (category, cause, confidence, evidence, "
            "suggested_next_step), primary_hypothesis, secondary_hypotheses, "
            "blast_radius_assessment, and suggested_next_steps. Do NOT grade from "
            "primary_hypothesis alone; a cause listed only under issue_categories "
            "still counts as identified.\n\n"
            "Score 0-5 (5 = essentially all required concurrent causes appear "
            "somewhere in the diagnosis with sound grounding; 0 = wrong or ignores "
            "the mix). Set correct=true only if most required causes are identified "
            "somewhere in that full JSON.\n"
            "In your reason, name which required ids were found vs missed; do not "
            "claim a miss for a cause that appears in issue_categories.\n\n"
            f"{known_block}\n\n"
            f"MODEL diagnosis (full JSON):\n{model_json}\n"
        )
    return (
        "You are grading a diagnostic model. Review the ENTIRE MODEL diagnosis JSON "
        "(issue_categories if present, primary_hypothesis, secondary_hypotheses, "
        "evidence, suggested_next_steps) against the known root cause. Score 0-5 "
        "(5 = correctly identifies the true root cause with sound reasoning; "
        "0 = wrong or hallucinated). Set correct=true only if the diagnosis "
        "matches the true root cause (primary or a clear issue_categories entry).\n\n"
        f"{known_block}\n\n"
        f"MODEL diagnosis (full JSON):\n{model_json}\n"
    )


def judge_case(judge_model, case: dict, diag: dict) -> dict:
    from langchain_core.messages import HumanMessage, SystemMessage

    verdict_prompt = build_judge_prompt(case, diag)
    try:
        v = judge_model.invoke(
            [
                SystemMessage(content="Output only the structured verdict."),
                HumanMessage(content=verdict_prompt),
            ]
        )
        return {"judge_score": v.score, "judge_correct": v.correct, "judge_reason": v.reason}
    except Exception as exc:  # noqa: BLE001
        return {"judge_score": None, "judge_correct": None, "judge_reason": f"judge failed: {exc}"}


def make_judge_model():
    from pydantic import BaseModel, Field

    from app.llm import get_chat_model

    class Verdict(BaseModel):
        correct: bool
        score: int = Field(ge=0, le=5)
        reason: str

    return get_chat_model().with_structured_output(Verdict)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def _host_model_snapshot() -> dict:
    from app.config import settings

    return settings.model_snapshot()


def _agent_model_snapshot(live_url: str) -> dict | None:
    """Pull chat/embed models from a live agent's /health when available."""
    import httpx

    try:
        r = httpx.get(f"{live_url.rstrip('/')}/health", timeout=5.0)
        r.raise_for_status()
        models = (r.json() or {}).get("models")
        return models if isinstance(models, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _models_for_summary(
    *, live: bool, live_url: str, results: list[dict], include_judge: bool
) -> dict:
    """Reference block: diagnosis models (+ judge when enabled)."""
    diagnosis = None
    if live:
        diagnosis = _agent_model_snapshot(live_url)
        if not diagnosis:
            for row in results:
                ex = row.get("llm_exchange") or {}
                if ex.get("chat_model") or ex.get("chat_provider"):
                    diagnosis = {
                        "chat_provider": ex.get("chat_provider"),
                        "chat_model": ex.get("chat_model"),
                        "embed_provider": ex.get("embed_provider"),
                        "embed_model": ex.get("embed_model"),
                    }
                    break
    else:
        diagnosis = _host_model_snapshot()

    out: dict = {"diagnosis": diagnosis}
    if include_judge:
        out["judge"] = _host_model_snapshot()
    return out


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def main() -> int:
    _load_eval_dotenv()
    ap = argparse.ArgumentParser(
        description=(
            "Diagnostic eval: score LLM root-cause ID from injected logs. "
            "Default is RAG-off (blind). Pass --rag or set EVAL_INCLUDE_RAG=true "
            "to inject runbook chunks. See eval/README.md."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python eval/run_blind_eval.py\n"
            "  python eval/run_blind_eval.py --rag --only jvm-heap-oom --judge\n"
            "  python eval/run_blind_eval.py --only jvm-heap-oom --judge\n"
            "  python eval/run_blind_eval.py --limit 3\n"
            "  python eval/run_blind_eval.py --merge --only "
            "postgres-connectivity,redis-connection,jvm-heap-oom --judge\n"
            "  python eval/run_blind_eval.py --live-url http://localhost:8001 "
            "--loki-url http://localhost:3100 --only redis-connection\n"
        ),
    )
    ap.add_argument(
        "--dataset",
        default=str(_DATASET),
        metavar="PATH",
        help=(
            "Path to the blind-eval YAML dataset "
            f"(default: {_DATASET.name} next to this script)"
        ),
    )
    ap.add_argument(
        "--out",
        default=str(_RESULTS_DIR),
        metavar="DIR",
        help="Directory for result JSON files (default: eval/results/)",
    )
    ap.add_argument(
        "--only",
        default="",
        metavar="IDS",
        help=(
            "Comma-separated case id(s) from the dataset to run "
            "(e.g. jvm-heap-oom or postgres-connectivity,redis-connection). "
            "Default: all cases"
        ),
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help=(
            "Run only the first N cases after --only filtering "
            "(0 = no limit; useful for smoke checks)"
        ),
    )
    ap.add_argument(
        "--judge",
        action="store_true",
        help=(
            "After keyword scoring, call a second LLM to grade the primary "
            "hypothesis vs expected.root_cause (0-5 score + correct bool)"
        ),
    )
    ap.add_argument(
        "--rag",
        action="store_true",
        help=(
            "Offline only: retrieve runbook chunks (same as agent rag_lookup) and "
            "inject them into the prompt. Also enabled when EVAL_INCLUDE_RAG=true "
            "in the environment / diagnostic-agent/.env. Ignored in --live-url mode "
            "(use the agent's AGENT_RAG_ENABLED instead)"
        ),
    )
    ap.add_argument(
        "--live-url",
        default="",
        metavar="URL",
        help=(
            "Base URL of a running diagnostic-agent. When set, switch to live mode: "
            "POST /alert for each case and score the returned diagnosis "
            "(e.g. http://localhost:8001). Omit for offline in-process mode"
        ),
    )
    ap.add_argument(
        "--loki-url",
        default="",
        metavar="URL",
        help=(
            "Loki base URL used only in live mode to push the case's log lines via "
            "POST /loki/api/v1/push before firing /alert "
            "(e.g. http://localhost:3100). Ignored in offline mode. "
            "If --live-url is set but --loki-url is omitted, logs are not injected"
        ),
    )
    ap.add_argument(
        "--merge",
        action="store_true",
        help=(
            "Combine the selected cases (after --only / --limit) into ONE request "
            "with interleaved logs and merged metrics — closer to a real mixed "
            "Loki sample. Requires at least 2 cases. Scores per-system hit rate"
        ),
    )
    ap.add_argument(
        "--merge-seed",
        type=int,
        default=42,
        metavar="N",
        help="RNG seed for shuffling interleaved logs when --merge is set (default: 42)",
    )
    args = ap.parse_args()

    cases = load_cases(Path(args.dataset))
    if args.only:
        wanted = {c.strip() for c in args.only.split(",") if c.strip()}
        cases = [c for c in cases if c["id"] in wanted]
        missing = wanted - {c["id"] for c in cases}
        if missing:
            print(f"WARNING: unknown --only id(s): {sorted(missing)}", file=sys.stderr)
    if args.limit:
        cases = cases[: args.limit]

    if args.merge:
        try:
            cases = [merge_cases(cases, seed=args.merge_seed)]
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    live = bool(args.live_url)
    include_rag = bool(args.rag or _env_flag("EVAL_INCLUDE_RAG")) and not live
    model = None if live else make_model()
    judge_model = make_judge_model() if args.judge else None

    if include_rag:
        store = get_rag_store()
        if not store.available:
            print(
                "WARNING: --rag / EVAL_INCLUDE_RAG set but RAG store unavailable "
                "(check AGENT_RAG_ENABLED, embeddings, runbooks/). Continuing with "
                "empty rag_context.\n"
            )

    results = []
    rag_label = "rag=agent" if live else ("rag=on" if include_rag else "rag=off")
    merge_label = " | merge=on" if args.merge else ""
    print(
        f"\nBlind eval: {len(cases)} cases | mode={'live' if live else 'offline'} "
        f"| {rag_label}{merge_label}"
        f"{' | judge=on' if args.judge else ''}\n"
    )
    for case in cases:
        cid = case["id"]
        try:
            if live:
                bundled = run_live(args.live_url, case, args.loki_url or None)
            else:
                bundled = run_offline(model, case, include_rag=include_rag)
            diag = bundled.get("diagnosis") or bundled
            exchange = bundled.get("llm_exchange") or {}
        except Exception as exc:  # noqa: BLE001
            diag = {"error": f"run failed: {exc}"}
            exchange = {}

        if case.get("merged_from"):
            score = score_merged_case(case, diag)
        else:
            score = score_case(case, diag)
        if judge_model is not None and not diag.get("error"):
            score.update(judge_case(judge_model, case, diag))

        usage = exchange.get("token_usage") or {}
        if usage.get("total_tokens") is not None:
            score["tokens_total"] = usage.get("total_tokens")
            score["tokens_in"] = usage.get("input_tokens")
            score["tokens_out"] = usage.get("output_tokens")
        # Prefer llm_exchange (new agent); fall back to diagnosis/_rag_used for
        # older live images that omit llm_exchange in the /alert response.
        score["rag_used"] = bool(
            exchange.get("rag_used")
            if "rag_used" in exchange
            else diag.get("_rag_used")
        )

        row = {
            "id": cid,
            "system": case.get("system"),
            "diagnosis": diag,
            "llm_exchange": exchange,
            "score": score,
        }
        if case.get("merged_from"):
            row["merged_from"] = list(case["merged_from"])
        results.append(row)

        flag = "OK " if score["identified"] else "MISS"
        judge_str = ""
        if "judge_score" in score:
            judge_str = f" judge={score['judge_score']}"
        tok = ""
        if score.get("tokens_total") is not None:
            tok = f" tok={score['tokens_total']}"
        rag_flag = " rag=yes" if score.get("rag_used") else " rag=no"
        if score.get("systems_total"):
            mix = (
                f" systems={score['systems_hit']}/{score['systems_total']}"
                f"({score['systems_hit_rate']})"
            )
        else:
            mix = ""
        print(
            f"  [{flag}] {cid:<28} system={case.get('system'):<22} "
            f"recall={score['keyword_recall']:<4} conf={score['confidence_note']}"
            f"{mix}{judge_str}{tok}{rag_flag}"
        )
        if score.get("per_system"):
            for p in score["per_system"]:
                mark = "hit" if p["hit"] else "miss"
                print(
                    f"         - {p['id']:<24} [{mark}] "
                    f"system={p.get('system')} recall={p['keyword_recall']}"
                )
        if score.get("error"):
            print(f"         ! {score['error']}")

    scored = [r for r in results if not r["score"].get("error")]
    n = len(scored) or 1
    id_acc = sum(1 for r in scored if r["score"]["identified"]) / n
    mean_recall = sum(r["score"]["keyword_recall"] for r in scored) / n
    summary = {
        "cases": len(results),
        "scored": len(scored),
        "mode": "live" if live else "offline",
        "rag_mode": "agent" if live else ("on" if include_rag else "off"),
        "merge": bool(args.merge),
        "models": _models_for_summary(
            live=live,
            live_url=args.live_url or "",
            results=results,
            include_judge=bool(args.judge),
        ),
        "identified_accuracy": round(id_acc, 3),
        "mean_keyword_recall": round(mean_recall, 3),
        "rag_used_rate": round(
            sum(1 for r in scored if r["score"].get("rag_used")) / n, 3
        ),
    }
    if args.merge and scored:
        hit_rates = [
            r["score"]["systems_hit_rate"]
            for r in scored
            if r["score"].get("systems_hit_rate") is not None
        ]
        if hit_rates:
            summary["mean_systems_hit_rate"] = round(
                sum(hit_rates) / len(hit_rates), 3
            )
    token_rows = [r for r in scored if r["score"].get("tokens_total") is not None]
    if token_rows:
        summary["tokens_total_sum"] = sum(r["score"]["tokens_total"] for r in token_rows)
        summary["tokens_in_sum"] = sum(r["score"].get("tokens_in") or 0 for r in token_rows)
        summary["tokens_out_sum"] = sum(
            r["score"].get("tokens_out") or 0 for r in token_rows
        )
    if args.judge:
        judged = [r for r in scored if r["score"].get("judge_score") is not None]
        if judged:
            summary["mean_judge_score"] = round(
                sum(r["score"]["judge_score"] for r in judged) / len(judged), 2
            )
            summary["judge_correct_rate"] = round(
                sum(1 for r in judged if r["score"].get("judge_correct")) / len(judged), 3
            )

    print("\nSummary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")

    Path(args.out).mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(args.out) / f"blind-eval-{stamp}.json"
    out_path.write_text(
        json.dumps({"summary": summary, "results": results}, indent=2), encoding="utf-8"
    )
    print(f"\nWrote {out_path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
