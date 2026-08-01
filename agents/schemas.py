"""Data contracts for the RCA agentic workflow (part 3).

Alert  = what part 2 (HyperDX static alerts) sends us.
RCA    = what we hand to part 4 (Langfuse showcase) and part 5 (chat agent).
"""

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

Metric = Literal["revenue", "fill_rate", "requests", "ctr", "ecpm"]


class Alert(BaseModel):
    alert_id: str
    metric: Metric
    window_start: datetime
    window_end: datetime
    direction: Literal["drop", "spike"]
    observed: float
    baseline: float
    source: str = "hyperdx"  # or "cli", "poller"

    # Upstream detection metadata (part-2 alert generation) — which
    # deterministic ClickHouse-native function fired and its score, e.g.
    # method="seriesDecomposeSTL_residual", score=-64954, params={"period": 7}.
    # Schema not finalized on their side yet, so all optional: an alert
    # without these still validates and falls back to our own significance
    # screen (see pipeline.py::screen()).
    detection_method: str | None = None
    detection_score: float | None = None
    detection_params: dict | None = None

    @field_validator("window_start", "window_end", mode="before")
    @classmethod
    def _epoch_ms_to_datetime(cls, v):
        """HyperDX's generic webhook sends {{startTime}}/{{endTime}} as JS
        epoch milliseconds (a number), not an ISO string. Left as a plain
        datetime field, pydantic would read a raw int/float as epoch
        *seconds* and land the date thousands of years in the future — so
        numeric input is normalized here before the default datetime
        parsing runs. ISO strings (CLI door) pass through unchanged."""
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v / 1000, tz=timezone.utc)
        return v


class Claim(BaseModel):
    text: str            # "fill rate fell 0.61 -> 0.34 in region=EU"
    value: float         # the number backing the claim
    tool_call_id: str    # which evidence entry it came from


class Check(BaseModel):
    """One entry in the 'what we checked' ledger — the bonus ask: state
    what was checked and ruled out, not just what was found. Broader than
    Claim: a check can be non-numeric (e.g. 'no dominant segment in
    device_model') and can be confirmed, not just ruled out."""
    check: str            # what was examined, e.g. "seasonality vs 4-week baseline"
    verdict: Literal["confirmed", "ruled_out", "inconclusive"]
    result: str            # plain-language finding
    value: float | None = None
    tool_call_id: str | None = None  # evidence id; None = not tool-backed (e.g. the upstream alert itself)


class RCA(BaseModel):
    rca_id: str = Field(default_factory=lambda: f"rca_{uuid4().hex[:8]}")
    alert_id: str
    metric: Metric
    window_start: datetime
    window_end: datetime
    factor: Literal["requests", "fill_rate", "render_rate", "ecpm", "unknown"]
    segments: list[str]           # ["region=EU", "device_model=Galaxy S23"]
    narrative: str                # <=150 words, plain language
    claims: list[Claim]
    checks: list[Check]           # what was checked, confirmed, and ruled out — and why
    confidence: Literal["high", "medium", "low"]
    trace_url: str = ""
    status: Literal["ok", "failed", "low_confidence"] = "ok"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
