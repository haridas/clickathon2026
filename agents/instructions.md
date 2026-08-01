# Manual walkthrough — RCA agentic pipeline

Run these by hand to see every stage of the system, end to end, without any
web server or webhook. All commands run from `~/clickathon/clickathon2026`.

```bash
cd ~/clickathon/clickathon2026
```

## 0. Prerequisites

- `.env` has working `CLICKHOUSE_*`, `ANTHROPIC_API_KEY`, and (optional)
  `LANGFUSE_*` values.
- `uv` installed (`brew install uv` or see uv docs).

## 1. Confirm the data is loaded

```bash
uv run python -c "
from agents import db
print('tables:', db.q('SHOW TABLES'))
print('row count:', db.q('SELECT count() AS n FROM ad_events')[0])
print('date range:', db.q('SELECT min(event_time) lo, max(event_time) hi FROM ad_events')[0])
"
```

Expect `ad_events` (9,000,000 rows), `events_enriched` (a view over it), and
`rca_results`. Data spans **2026-06-01 to 2026-07-05**.

## 2. Pick a window and preview it

Before running the full pipeline, sanity-check a candidate window with the
`metric_overview` tool directly — it shows all 5 metrics vs baseline with a
z-score, so you can spot something worth investigating:

```bash
uv run python -c "
from agents.tools import metric_overview
import json
d = json.loads(metric_overview.invoke({
    'window_start': '2026-06-20T09:00',
    'window_end':   '2026-06-20T12:00',
}))
print(json.dumps(d['metrics'], indent=2))
"
```

Look for a metric with `|z| > 2` — that's a real deviation, not noise. Note
its `observed` and `baseline` values for the next step.

## 3. Run the full 4-agent pipeline

```bash
uv run python -m agents.cli investigate \
  --metric ecpm \
  --from 2026-06-20T09:00 --to 2026-06-20T12:00 \
  --direction drop \
  --observed 2.4037 --baseline 2.4826
```

Swap in your own window/metric/observed/baseline from step 2. `--metric` is
one of `revenue | fill_rate | requests | ctr | ecpm`; `--direction` is
`drop | spike`.

This runs, in order: **Triage** (confirms the alert) → **Investigator**
(names the factor + guilty segment) → **Skeptic** (re-verifies + checks
seasonality) → **Writer** (produces the final report, numbers only from
tool evidence). Takes roughly 60–90 seconds. Prints the full RCA as JSON —
`narrative`, `claims` (each with the tool-call id it came from), `ruled_out`,
`confidence`, and a `trace_url`.

If `confidence` comes back `low` with `status: low_confidence` and the
narrative ends in `[guardrail: ...]`, that means the automatic numeral
guardrail caught a claim it couldn't match to real tool evidence — the
report is intentionally being flagged as untrustworthy rather than shipped
silently.

## 4. Check the outputs landed

**ClickHouse:**
```bash
uv run python -c "
from agents import db
rows = db.q('SELECT rca_id, metric, factor, confidence, status, created_at FROM rca_results ORDER BY created_at DESC LIMIT 5')
for r in rows: print(r)
"
```

**index.md** (repo root, append-only log):
```bash
tail -12 index.md
```

## 5. View the trace

Open the `trace_url` printed in step 3's JSON output in a browser. It shows
the whole run as a tree — each of the 4 agent stages, every tool call each
one made (with the exact SQL text and the rows it returned), and every
model call (tokens, cost, latency). This is the judge-facing proof that
every number is traceable back to a real query.

## 6. (Optional) Call individual tools directly

To poke at one piece without running the whole pipeline — e.g. to hand-check
a number, or explore a dimension:

```bash
uv run python -c "
from agents.tools import contribution_by_dimension
import json
d = json.loads(contribution_by_dimension.invoke({
    'dimension': 'ad_format',
    'window_start': '2026-06-20T09:00',
    'window_end':   '2026-06-20T12:00',
    'metric': 'ecpm',
}))
print(json.dumps(d, indent=2))
"
```

Available tools (all in `agents/tools.py`): `metric_overview`,
`factor_decompose`, `contribution_by_dimension`, `drilldown_filtered`,
`seasonality_check`, `verify_claim`. Available dimensions right now:
`ad_format`, `app_id`, `advertiser_id`, `geo_device_id` (the full 9-dim
plan needs the still-broken `geo_device.csv` etc. — see CONTEXT.md).

## 7. Run the same pipeline via the FastAPI webhook

Instead of the CLI, you can drive `run_rca()` over HTTP — this is the real
integration point for the HyperDX alert (part 2).

Start the server (leave this running in one terminal):

```bash
uv run uvicorn agents.api:app --host 0.0.0.0 --port 9100
```

In another terminal, check it's alive:

```bash
curl -sS http://localhost:9100/health
```

Fire an alert (same fields as the CLI flags, but as JSON — `window_start`
and `window_end` need seconds, e.g. `2026-06-20T09:00:00`):

```bash
curl -sS -X POST http://localhost:9100/alert \
  -H "Content-Type: application/json" \
  -d '{
    "alert_id": "manual_test_001",
    "metric": "ecpm",
    "window_start": "2026-06-20T09:00:00",
    "window_end": "2026-06-20T12:00:00",
    "direction": "drop",
    "observed": 2.4037,
    "baseline": 2.4826,
    "source": "hyperdx"
  }' | python3 -m json.tool
```

This blocks for ~60-90s (the pipeline runs synchronously) then returns the
full RCA JSON, same shape as the CLI. A few things to know:

- Re-POSTing the same `alert_id` returns **409** (already processed) unless
  you add `?force=true` to the URL.
- A malformed body returns **422** with the validation errors.
- The `Alert` schema is in `agents/schemas.py` — this endpoint currently
  accepts it directly (the real HyperDX payload shape isn't finalized yet;
  see CONTEXT.md).

Stop the server with `Ctrl+C` in its terminal (or `kill <pid>` if you
started it in the background).

## 8. Run the automated tool test suite

```bash
uv run python tests/test_tools.py
```

Should print `all Phase 2 tool tests passed` — validates each tool returns
valid JSON, the factor-decompose shares sum to ~100%, injection inputs
(`dimension="drop table"`) return error JSON without executing, and one
`contribution_by_dimension` result is independently reproducible by hand.
