"""FastAPI webhook entry door — POST /alert on port 9100.

Real integration point for part 2 (HyperDX). Builds the same `Alert` the CLI
door builds and calls the same `run_rca()` — one pipeline, three doors.

The exact HyperDX payload shape isn't agreed with the part-2 teammate yet
(see CONTEXT.md), so this endpoint accepts the `Alert` schema directly. Once
a real HyperDX payload is captured, add a small adapter function that maps
it onto `Alert` and call it before `run_rca()` — nothing else here changes.

Run:
  uv run uvicorn agents.api:app --host 0.0.0.0 --port 9100
"""

from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from agents.outputs import deliver, rca_exists
from agents.pipeline import run_rca
from agents.schemas import RCA, Alert

app = FastAPI(title="RCA Agentic Workflow", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/alert", response_model=RCA)
def alert(payload: Alert, force: bool = False) -> RCA:
    """Run the full Triage -> Investigator -> Skeptic -> Writer pipeline on
    an alert and persist the result (rca_results + index.md). Synchronous —
    a run takes roughly 60-90s; the caller's HTTP client should set a
    generous read timeout. `?force=true` re-runs even if this alert_id was
    already processed (default: skip and 409)."""
    if not force and rca_exists(payload.alert_id):
        raise HTTPException(
            status_code=409,
            detail=f"alert_id {payload.alert_id!r} already has an RCA — "
                    "pass ?force=true to re-run",
        )
    rca = run_rca(payload)
    deliver(rca)
    return rca


@app.exception_handler(ValidationError)
def handle_validation_error(_, exc: ValidationError):
    raise HTTPException(status_code=422, detail=exc.errors())
