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

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from agents import narrator_outputs
from agents.narrator import _derive_alert_id, run_narrator
from agents.outputs import deliver, rca_exists
from agents.pipeline import run_rca
from agents.schemas import RCA, Alert, NarratorRCA

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


@app.post("/narrate", response_model=NarratorRCA)
def narrate_alert(payload: dict[str, Any], force: bool = False) -> NarratorRCA:
    """The Narrator door — accepts Part 2's diagnose() evidence dict
    verbatim (no reshaping needed on their side), runs one LLM call over
    it (no tools, no drill-down — that's already done), and persists the
    result to narrator_results + index.md. This is the second webhook the
    adapter fires per incident, alongside /alert; both rows share the same
    alert_id (derived the same way the adapter's own build_alert() does)
    so they're directly comparable for the same incident.

    400 if the evidence has no flagged_factor (nothing to summarize —
    mirrors the adapter's own build_alert() early-return). `?force=true`
    re-runs even if this alert_id already has a narrator_results row."""
    if payload.get("flagged_factor") is None:
        raise HTTPException(
            status_code=400,
            detail="evidence.flagged_factor is None — nothing to summarize",
        )
    try:
        alert_id = payload.get("alert_id") or _derive_alert_id(payload)
    except (KeyError, TypeError) as e:
        raise HTTPException(
            status_code=422, detail=f"malformed evidence payload: {e}",
        ) from e
    if not force and narrator_outputs.rca_exists(alert_id):
        raise HTTPException(
            status_code=409,
            detail=f"alert_id {alert_id!r} already has a narrator RCA — "
                    "pass ?force=true to re-run",
        )
    rca = run_narrator(payload, alert_id=alert_id)
    narrator_outputs.deliver(rca)
    return rca


@app.get("/narrations", response_model=list[NarratorRCA])
def list_narrations(limit: int = 100) -> list[NarratorRCA]:
    """Read door for the dashboard — most recent Narrator RCAs first.
    Read-only, no side effects; safe to poll."""
    return narrator_outputs.list_rca(limit=limit)


@app.exception_handler(ValidationError)
def handle_validation_error(_, exc: ValidationError):
    raise HTTPException(status_code=422, detail=exc.errors())


# Dashboard UI — static, fetches /narrations itself. Mounted last so it
# never shadows the API routes above (Starlette matches routes in
# registration order; a Mount at "/" registered first would swallow
# everything).
_STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="dashboard")
