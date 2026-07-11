"""FastAPI trigger server.

Alertmanager POSTs firing alerts to /alert. The agent runs the diagnostic graph
for each firing alert and delivers an audit record, Grafana annotation, and
optional diagnostic email.

Purely reactive -- no polling.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("diagnostic-agent")

# Built lazily at startup so import (and tests) don't require live backends/LLM.
_agent = None


def get_agent():
    global _agent
    if _agent is None:
        from .agent import DiagnosticAgent

        _agent = DiagnosticAgent()
    return _agent


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        get_agent()  # warm clients + RAG store once
    except Exception as exc:  # noqa: BLE001 - keep server up; report on /health
        logger.error("agent init failed at startup: %s", exc)
    yield


app = FastAPI(title="publishi.ai diagnostic agent", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "agent_initialized": _agent is not None}


@app.post("/alert")
async def handle_alert(request: Request):
    payload = await request.json()
    alerts = payload.get("alerts", [])
    reports = []
    agent = get_agent()
    for alert in alerts:
        if alert.get("status") != "firing":
            continue
        try:
            report = agent.diagnose(alert)
            reports.append(report)
        except Exception as exc:  # noqa: BLE001 - one bad alert must not 500 the batch
            logger.exception("diagnosis failed for alert: %s", exc)
            reports.append({"error": str(exc), "labels": alert.get("labels", {})})
    return {"status": "processed", "count": len(reports), "reports": reports}
