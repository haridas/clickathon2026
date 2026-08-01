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


# ---------------------------------------------------------------------------
# Narrator pipeline — a second, separate RCA path (agents/narrator.py).
#
# Part 2's adapter (~/inmobi_agent/diagnose.py on the VM) already does the
# deterministic detection + drill-down (Detector + Attributor, per the
# team's "Agents — InMobi Root-Cause Analyst" spec) — the Narrator's ONLY
# job is turning its `findings` into plain language, one LLM call, no
# tools. These schemas are intentionally separate from RCA/Claim/Check
# above (that trio is shaped around the tool-calling pipeline's per-
# tool-call evidence ids, which don't apply here — there's exactly one
# evidence blob per incident, not N tool calls).

class FoundSegment(BaseModel):
    dimension: str
    segment: str
    contribution_pct: float   # % of the metric's total deviation this segment explains
    actual: float
    baseline: float


class RuledOutEntry(BaseModel):
    dimension: str
    segment: str | None = None   # None when ruling out a whole factor, not a segment
    note: str
    value: float | None = None   # z-score or contribution backing the ruling, if any


class Findings(BaseModel):
    """Deterministic output of the Detector+Attributor step — every field
    here is copied verbatim from Part 2's diagnose() evidence, never
    computed or estimated by an LLM."""
    metric: Metric
    window_start: datetime
    window_end: datetime
    baseline: float
    actual: float
    deviation_pct: float
    detector: str
    found: list[FoundSegment]
    ruled_out: list[RuledOutEntry]
    corroboration: dict | None = None


class NarratorRCA(BaseModel):
    rca_id: str = Field(default_factory=lambda: f"narr_{uuid4().hex[:8]}")
    alert_id: str
    findings: Findings
    narrative: str
    confidence: Literal["high", "medium", "low"]
    trace_url: str = ""
    status: Literal["ok", "failed", "low_confidence"] = "ok"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
