"""Data contracts for the RCA agentic workflow (part 3).

Alert  = what part 2 (HyperDX static alerts) sends us.
RCA    = what we hand to part 4 (Langfuse showcase) and part 5 (chat agent).
"""

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

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


class Claim(BaseModel):
    text: str            # "fill rate fell 0.61 -> 0.34 in region=EU"
    value: float         # the number backing the claim
    tool_call_id: str    # which evidence entry it came from


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
    ruled_out: list[Claim]
    confidence: Literal["high", "medium", "low"]
    trace_url: str = ""
    status: Literal["ok", "failed", "low_confidence"] = "ok"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
