"""Blind diagnostic eval for the diagnostic-agent LLM.

Goal: measure how well the configured LLM identifies a root cause from LOGS
ALONE, with NO runbook/RAG context and NO hints. This isolates the model's
reasoning from the local knowledge base (the whole point of the test).

It reuses the *real* system prompt (app/graph/prompts.py), the *real* model
factory (app/llm.py), the *real* output schema (app/graph/schema.py) and the
*real* Loki log formatting (app/clients/loki.py), so the offline result closely
matches what the deployed agent would produce -- minus the RAG context, which is
forced to "none".

Two modes
---------
offline (default)
    Build the exact correlate prompt in-process and call the model directly.
    Fast, deterministic-ish (temperature 0.1), needs only LLM credentials.

live (--live-url http://localhost:8001)
    Optionally push each case's logs into Loki (--loki-url http://localhost:3100),
    then POST an Alertmanager-shaped alert to the running agent's /alert and read
    the returned diagnosis. Exercises the full pipeline. For a truly blind run,
    start the agent with AGENT_RAG_ENABLED=false.

Scoring
-------
- identified      : did the primary hypothesis name the right system/cause?
                    (any expected cause_keyword present in primary cause+evidence)
- keyword_recall  : fraction of expected cause_keywords found anywhere in the diagnosis
- grounded        : did the evidence cite provided log tokens (anti-hallucination)?
- confidence_note : the model's self-reported confidence (calibration signal)
- judge_score     : optional 0-5 LLM-as-judge score vs the ground-truth root_cause

Usage
-----
    cd diagnostic-agent
    # offline, whatever AGENT_CHAT_* provider is configured in .env / env
    python eval/run_blind_eval.py
    python eval/run_blind_eval.py --judge
    python eval/run_blind_eval.py --only postgres-connectivity,redis-connection
    # live pipeline (agent must be up; start it with AGENT_RAG_ENABLED=false)
    python eval/run_blind_eval.py --live-url http://localhost:8001 --loki-url http://localhost:3100
"""
from __future__ import annotations

import argparse
import json
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


# --------------------------------------------------------------------------
# dataset
# --------------------------------------------------------------------------
def load_cases(path: Path) -> list[dict]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = raw.get("cases") or []
    if not cases:
        raise ValueError(f"No cases in {path}")
    return cases


def format_logs(lines: list[str]) -> list[str]:
    """Format raw JSON log lines exactly like the live retrieve node does."""
    from app.clients.loki import LokiClient

    # (ts_ns, line) pairs; @timestamp inside the JSON overrides the dummy ts.
    pairs = [(str(1_000_000_000 + i), line) for i, line in enumerate(lines)]
    return LokiClient.format_log_entries(pairs)


def build_prompt(case: dict) -> tuple[str, str]:
    """Return (system_prompt, human_content) mirroring nodes.correlate exactly,
    but with the runbook/RAG context forced to 'none'."""
    from app.graph.prompts import SYSTEM_PROMPT

    alert = case.get("alert", {})
    logs = format_logs(case.get("logs", []))
    human = (
        f"Alert: {alert.get('alertname')} on {alert.get('service')} "
        f"(severity: {alert.get('severity')})\n"
        f"Suspected module: {alert.get('module') or 'unknown'}\n"
        f"Dependencies checked: {case.get('dependencies', [])}\n"
        f"Metrics snapshot: {json.dumps(case.get('metrics', {}))}\n"
        f"Recent error/warn logs (sample): {logs[:10]}\n"
        f"Runbook / past-incident context: none\n"
        f"Downstream services at risk: {case.get('blast_radius', [])}"
    )
    return SYSTEM_PROMPT, human


# --------------------------------------------------------------------------
# model runners
# --------------------------------------------------------------------------
def make_model():
    from app.graph.schema import Diagnosis
    from app.llm import get_chat_model

    return get_chat_model().with_structured_output(Diagnosis, include_raw=True)


def run_offline(model, case: dict) -> dict:
    """Return {diagnosis, llm_exchange} for offline scoring + prompt/token audit."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.llm_usage import extract_token_usage

    system, human = build_prompt(case)
    result = model.invoke([SystemMessage(content=system), HumanMessage(content=human)])
    parsed = result.get("parsed") if isinstance(result, dict) else None
    raw_msg = result.get("raw") if isinstance(result, dict) else None
    raw = getattr(raw_msg, "content", "") or ""
    token_usage = extract_token_usage(raw_msg)
    exchange = {
        "system_prompt": system,
        "user_prompt": human,
        "rag_context": "",
        "rag_used": False,
        "token_usage": token_usage,
        "llm_raw": raw,
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


def push_logs_to_loki(loki_url: str, case: dict) -> None:
    """Push the case's log lines into Loki under {service=...} so the live agent
    picks them up via its query_range."""
    import httpx

    svc = case.get("alert", {}).get("service", "platform-service")
    now_ns = int(time.time() * 1e9)
    values = [
        [str(now_ns + i * 1_000_000), line]  # 1ms apart, newest last
        for i, line in enumerate(case.get("logs", []))
    ]
    body = {"streams": [{"stream": {"service": svc, "level": "ERROR"}, "values": values}]}
    with httpx.Client(timeout=10.0) as client:
        r = client.post(f"{loki_url.rstrip('/')}/loki/api/v1/push", json=body)
        r.raise_for_status()


def run_live(live_url: str, case: dict, loki_url: str | None) -> dict:
    """Return {diagnosis, llm_exchange} from a running agent's /alert response."""
    import httpx

    if loki_url:
        push_logs_to_loki(loki_url, case)
        time.sleep(3)  # let Loki ingest before the agent queries

    payload = {
        "alerts": [
            {
                "status": "firing",
                "labels": case.get("alert", {}),
                "annotations": {"summary": f"blind-eval {case['id']}"},
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
    # surface rag_used so the operator can confirm the run was truly blind
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
    parts.append(diag.get("blast_radius_assessment", ""))
    parts.extend(diag.get("suggested_next_steps", []) or [])
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


def judge_case(judge_model, case: dict, diag: dict) -> dict:
    from langchain_core.messages import HumanMessage, SystemMessage

    primary = diag.get("primary_hypothesis", {}) or {}
    verdict_prompt = (
        "You are grading a diagnostic model. Compare the model's PRIMARY hypothesis "
        "to the KNOWN root cause. Score 0-5 (5 = correctly identifies the true root "
        "cause with sound reasoning; 0 = wrong or hallucinated). Set correct=true "
        "only if the primary hypothesis matches the true root cause.\n\n"
        f"KNOWN root cause: {case.get('expected', {}).get('root_cause', '')}\n\n"
        f"MODEL primary cause: {primary.get('cause', '')}\n"
        f"MODEL evidence: {primary.get('evidence', '')}\n"
        f"MODEL confidence_note: {diag.get('confidence_note')}"
    )
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
def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Blind diagnostic eval: score how well the LLM identifies a root cause "
            "from logs alone (RAG context forced to none). "
            "See eval/README.md for full parameter docs."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python eval/run_blind_eval.py\n"
            "  python eval/run_blind_eval.py --only jvm-heap-oom --judge\n"
            "  python eval/run_blind_eval.py --limit 3\n"
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
    args = ap.parse_args()

    cases = load_cases(Path(args.dataset))
    if args.only:
        wanted = {c.strip() for c in args.only.split(",") if c.strip()}
        cases = [c for c in cases if c["id"] in wanted]
    if args.limit:
        cases = cases[: args.limit]

    live = bool(args.live_url)
    model = None if live else make_model()
    judge_model = make_judge_model() if args.judge else None

    results = []
    print(f"\nBlind eval: {len(cases)} cases | mode={'live' if live else 'offline'}"
          f"{' | judge=on' if args.judge else ''}\n")
    for case in cases:
        cid = case["id"]
        try:
            if live:
                bundled = run_live(args.live_url, case, args.loki_url or None)
            else:
                bundled = run_offline(model, case)
            diag = bundled.get("diagnosis") or bundled
            exchange = bundled.get("llm_exchange") or {}
        except Exception as exc:  # noqa: BLE001
            diag = {"error": f"run failed: {exc}"}
            exchange = {}

        score = score_case(case, diag)
        if judge_model is not None and not diag.get("error"):
            score.update(judge_case(judge_model, case, diag))

        # Aggregate token totals when present
        usage = exchange.get("token_usage") or {}
        if usage.get("total_tokens") is not None:
            score["tokens_total"] = usage.get("total_tokens")
            score["tokens_in"] = usage.get("input_tokens")
            score["tokens_out"] = usage.get("output_tokens")

        results.append(
            {
                "id": cid,
                "system": case.get("system"),
                "diagnosis": diag,
                "llm_exchange": exchange,
                "score": score,
            }
        )

        flag = "OK " if score["identified"] else "MISS"
        judge_str = ""
        if "judge_score" in score:
            judge_str = f" judge={score['judge_score']}"
        tok = ""
        if score.get("tokens_total") is not None:
            tok = f" tok={score['tokens_total']}"
        print(
            f"  [{flag}] {cid:<28} system={case.get('system'):<22} "
            f"recall={score['keyword_recall']:<4} conf={score['confidence_note']}"
            f"{judge_str}{tok}"
        )
        if score.get("error"):
            print(f"         ! {score['error']}")

    # aggregate
    scored = [r for r in results if not r["score"].get("error")]
    n = len(scored) or 1
    id_acc = sum(1 for r in scored if r["score"]["identified"]) / n
    mean_recall = sum(r["score"]["keyword_recall"] for r in scored) / n
    summary = {
        "cases": len(results),
        "scored": len(scored),
        "identified_accuracy": round(id_acc, 3),
        "mean_keyword_recall": round(mean_recall, 3),
    }
    token_rows = [r for r in scored if r["score"].get("tokens_total") is not None]
    if token_rows:
        summary["tokens_total_sum"] = sum(r["score"]["tokens_total"] for r in token_rows)
        summary["tokens_in_sum"] = sum(r["score"].get("tokens_in") or 0 for r in token_rows)
        summary["tokens_out_sum"] = sum(r["score"].get("tokens_out") or 0 for r in token_rows)
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
