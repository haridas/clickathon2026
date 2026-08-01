"""RCA outputs: the rca_results table (for part 4/5) and index.md (part 5 context).

If ClickHouse isn't configured yet (no CLICKHOUSE_HOST in .env), the DB write
is skipped with a loud warning so the pipeline stays runnable during dev.
"""

import json
import sys
from pathlib import Path

from agents import db
from agents.schemas import RCA

INDEX_MD = Path(__file__).resolve().parent.parent / "index.md"

DDL = """
CREATE TABLE IF NOT EXISTS rca_results (
  rca_id        String,
  alert_id      String,
  created_at    DateTime64(3, 'UTC'),
  metric        LowCardinality(String),
  window_start  DateTime('UTC'),
  window_end    DateTime('UTC'),
  factor        LowCardinality(String),
  segments      Array(String),
  narrative     String,
  claims        String,
  checks        String,
  confidence    LowCardinality(String),
  trace_url     String,
  status        LowCardinality(String)
) ENGINE = MergeTree ORDER BY created_at
"""


def ensure_table() -> None:
    db.command(DDL)


def rca_exists(alert_id: str) -> bool:
    rows = db.q(
        "SELECT count() AS n FROM rca_results WHERE alert_id = {aid:String}",
        {"aid": alert_id},
    )
    return rows[0]["n"] > 0


def write_rca(rca: RCA) -> None:
    if not db.configured():
        print("WARNING: CLICKHOUSE_HOST not set — skipping rca_results write",
              file=sys.stderr)
        return
    ensure_table()
    db.client().insert(
        "rca_results",
        [[
            rca.rca_id, rca.alert_id, rca.created_at,
            rca.metric, rca.window_start, rca.window_end,
            rca.factor, rca.segments, rca.narrative,
            json.dumps([c.model_dump() for c in rca.claims]),
            json.dumps([c.model_dump() for c in rca.checks]),
            rca.confidence, rca.trace_url, rca.status,
        ]],
        column_names=[
            "rca_id", "alert_id", "created_at", "metric", "window_start",
            "window_end", "factor", "segments", "narrative", "claims",
            "checks", "confidence", "trace_url", "status",
        ],
    )


def append_index(rca: RCA) -> None:
    if rca.checks:
        ledger = "\n".join(
            f"  - [{c.verdict}] {c.check} — {c.result}" for c in rca.checks
        )
    else:
        ledger = "  - none recorded"
    block = f"""
## RCA {rca.created_at:%Y-%m-%dT%H:%M} · {rca.metric} · alert {rca.alert_id}
- window: {rca.window_start:%Y-%m-%d %H:%M} – {rca.window_end:%H:%M} UTC
- root cause: {"; ".join(rca.segments) or "broad-based"} ({rca.narrative.splitlines()[0][:120]})
- factor: {rca.factor} · confidence: {rca.confidence} · status: {rca.status}
- checks (what was checked, confirmed, and ruled out):
{ledger}
- trace: {rca.trace_url or "(no trace)"}
- rca_id: {rca.rca_id} (full evidence in rca_results)
"""
    if not INDEX_MD.exists():
        INDEX_MD.write_text(
            "# RCA index\n\nAppend-only log of every investigation. "
            "Newest at the bottom. Context source for the part-5 chat agent.\n"
        )
    with INDEX_MD.open("a") as f:
        f.write(block)


def deliver(rca: RCA) -> None:
    write_rca(rca)
    append_index(rca)
