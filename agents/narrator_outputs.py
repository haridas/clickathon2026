"""Narrator outputs: its own narrator_results table (separate from
rca_results — the tool-calling pipeline and the Narrator are two distinct
approaches to the same incidents, kept independently queryable/comparable)
plus a shared index.md block (still "context source for the part-5 chat
agent", clearly headed so it's visually distinct from ## RCA blocks).
"""

import json
import sys
from pathlib import Path

from agents import db
from agents.schemas import NarratorRCA

INDEX_MD = Path(__file__).resolve().parent.parent / "index.md"

DDL = """
CREATE TABLE IF NOT EXISTS narrator_results (
  rca_id        String,
  alert_id      String,
  created_at    DateTime64(3, 'UTC'),
  metric        LowCardinality(String),
  window_start  DateTime('UTC'),
  window_end    DateTime('UTC'),
  baseline      Float64,
  actual        Float64,
  deviation_pct Float64,
  detector      String,
  found         String,
  ruled_out     String,
  corroboration String,
  narrative     String,
  confidence    LowCardinality(String),
  trace_url     String,
  status        LowCardinality(String)
) ENGINE = MergeTree ORDER BY created_at
"""


def ensure_table() -> None:
    db.command(DDL)


def rca_exists(alert_id: str) -> bool:
    ensure_table()
    rows = db.q(
        "SELECT count() AS n FROM narrator_results WHERE alert_id = {aid:String}",
        {"aid": alert_id},
    )
    return rows[0]["n"] > 0


def write_rca(rca: NarratorRCA) -> None:
    if not db.configured():
        print("WARNING: CLICKHOUSE_HOST not set — skipping narrator_results write",
              file=sys.stderr)
        return
    ensure_table()
    f = rca.findings
    db.client().insert(
        "narrator_results",
        [[
            rca.rca_id, rca.alert_id, rca.created_at,
            f.metric, f.window_start, f.window_end,
            f.baseline, f.actual, f.deviation_pct, f.detector,
            json.dumps([s.model_dump() for s in f.found]),
            json.dumps([r.model_dump() for r in f.ruled_out]),
            json.dumps(f.corroboration) if f.corroboration is not None else "",
            rca.narrative, rca.confidence, rca.trace_url, rca.status,
        ]],
        column_names=[
            "rca_id", "alert_id", "created_at", "metric", "window_start",
            "window_end", "baseline", "actual", "deviation_pct", "detector",
            "found", "ruled_out", "corroboration", "narrative", "confidence",
            "trace_url", "status",
        ],
    )


def append_index(rca: NarratorRCA) -> None:
    f = rca.findings
    if f.found:
        found_lines = "\n".join(
            f"  - {s.dimension}={s.segment}: {s.contribution_pct:+.1f}% of the deviation "
            f"(actual {s.actual:g} vs baseline {s.baseline:g})" for s in f.found
        )
    else:
        found_lines = "  - none crossed the dominant-driver threshold"
    if f.ruled_out:
        ruled_lines = "\n".join(
            f"  - {r.dimension}" + (f"={r.segment}" if r.segment else "") + f" — {r.note}"
            for r in f.ruled_out
        )
    else:
        ruled_lines = "  - none recorded"
    corrob = (f"corroborated={f.corroboration.get('corroborated')}"
              if f.corroboration else "not available")
    block = f"""
## Narrator RCA {rca.created_at:%Y-%m-%dT%H:%M} · {f.metric} · alert {rca.alert_id}
- window: {f.window_start:%Y-%m-%d %H:%M} – {f.window_end:%H:%M} UTC
- detector: {f.detector}
- deviation: {f.actual:g} vs baseline {f.baseline:g} ({f.deviation_pct:+.2f}%)
- narrative: {rca.narrative}
- found:
{found_lines}
- ruled out:
{ruled_lines}
- native cross-validation: {corrob}
- confidence: {rca.confidence} · status: {rca.status}
- trace: {rca.trace_url or "(no trace)"}
- rca_id: {rca.rca_id} (full findings in narrator_results)
"""
    if not INDEX_MD.exists():
        INDEX_MD.write_text(
            "# RCA index\n\nAppend-only log of every investigation. "
            "Newest at the bottom. Context source for the part-5 chat agent.\n"
        )
    with INDEX_MD.open("a") as f_:
        f_.write(block)


def deliver(rca: NarratorRCA) -> None:
    write_rca(rca)
    append_index(rca)
