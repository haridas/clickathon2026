# Part 3 — RCA Agentic Workflow · Session Context

> Handoff file for continuing work on the `agents/` package. Update the
> "Current state" and "Next steps" sections at the end of every session.
> Last updated: 2026-08-01 ~19:45 IST (Phase 4 complete — FastAPI webhook live).

## What this is

Part 3 of the team's 4-part Click-a-Thon system (InMobi problem):
HyperDX alert (part 2) → **webhook → 4-agent RCA pipeline → rca_results +
index.md** (this package) → Langfuse showcase (part 4). Part 5 (chat on RCA)
comes later and reads `index.md` + `rca_results`.

**Full plan (source of truth, has phase checklists):**
`~/clickathon/specs/rca-agentic-workflow-langchain.html` — open in a browser.
Master team plan: `~/clickathon/specs/inmobi-rca-analyst-24h-plan.html`.

## Agreed design (do not relitigate)

- **Constrained tools, no free SQL**: agents pick *which* parameterized SQL
  template to run, never write SQL. Every number in an RCA is a ClickHouse
  result by construction.
- **Pipeline of specialists**: Triage (FAST model) → Investigator (STRONG,
  max 8 tool calls) → Skeptic (STRONG, re-verifies + ruled-out ledger) →
  Writer (FAST, no tools, `response_format=RCA`). Plain Python sequence,
  not LangGraph (optional upgrade later).
- **Entry doors**: CLI (dev/fallback) → FastAPI `POST /alert` on port 9100
  (real integration, HyperDX adapter) → poller over an alerts table (reserve).
  All three build one `Alert` and call `run_rca()`.
- **Models via .env**: `RCA_MODEL_STRONG` / `RCA_MODEL_FAST`
  (anthropic:claude-sonnet-5 / claude-haiku-4-5); provider swap = edit strings.
- ~~Temperature 0 everywhere~~ **omit `temperature` entirely** — current Claude
  models 400 on it ("deprecated"); `recursion_limit=15`; ~60s budget per RCA;
  tools return top-20 rows max and include the executed SQL in a `"sql"` field.

## Current state (end of Phase 4 — pipeline + webhook live, all verified working)

| Piece | Status |
|---|---|
| `pyproject.toml` + `uv.lock` | ✓ langchain, langchain-anthropic, langgraph, clickhouse-connect, langfuse, fastapi, uvicorn, pydantic, python-dotenv (Python 3.14) |
| `agents/schemas.py` | ✓ `Alert`, `Claim`, `RCA` contracts (shared with parts 2/4/5) |
| `agents/db.py` | ✓ single client, parameterized queries only, reads `.env` |
| `agents/outputs.py` | ✓ `deliver(rca)` → rca_results row + index.md block; DDL is CREATE IF NOT EXISTS |
| `agents/pipeline.py` | **stub** — returns canned RCA; Phase 3 replaces the body, keep the signature |
| `agents/cli.py` | ✓ `uv run python -m agents.cli investigate --metric revenue --from 2026-06-18T14:00 --to 2026-06-18T17:00 --observed 41200 --baseline 47000` |
| ClickHouse Cloud | ✓ reachable (host in `.env`); `rca_results` created, 1 stub row |
| `index.md` (repo root) | ✓ append-only RCA log, 2 stub entries |
| `ad_events` in ClickHouse | ✓ **9,000,000 rows loaded** (2026-06-01 → 2026-07-05) via `agents/load_events.py` (resumable, 200k-row batches — 1M-row inserts time out on this uplink) |
| `events_enriched` | ✓ VIEW over `ad_events` — P1 teammate can swap in a real enriched table, tools won't change |
| `agents/tools.py` | ✓ all 6 tools: `metric_overview`, `factor_decompose`, `contribution_by_dimension`, `drilldown_filtered`, `seasonality_check`, `verify_claim`. Allowlisted dims (only `ad_format`, `app_id`, `advertiser_id`, `geo_device_id` until LFS CSVs recovered), server-side binding, top-20, `"sql"`+`"sql_params"` in every output, errors as JSON. Baseline = same weekday+hours, trailing 4 wks, `quantileExactInclusive(0.5)` (matches Python `statistics.median` — plain `medianExact` does NOT for even counts) |
| `tests/test_tools.py` | ✓ `uv run python tests/test_tools.py` — 6 tools valid JSON in ~2s, factor shares sum to 100%, injection inputs → error JSON, verify_claim ≡ metric_overview. Top ad_format contribution hand-checked against independent SQL — identical |
| `agents/pipeline.py` | ✓ **Phase 3 done.** Real 4-agent sequence: Triage (haiku, `metric_overview`) → Investigator (sonnet, decompose/contribution/drilldown, ≤8 calls) → Skeptic (sonnet, seasonality/verify_claim) → Writer (haiku, `.with_structured_output(WriterOutput)`, no tools). Shared `evidence` list (tool outputs harvested from each stage's message history, ids `t1, t2, ...`). Numeral guardrail (`_guardrail()`) rejects any Writer claim whose value isn't literally present in its cited evidence entry — downgrades to `confidence=low, status=low_confidence` and appends a warning to the narrative rather than silently trusting derived math. One Langfuse trace per run via `lf.start_as_current_observation(as_type="span")` + `root.set_trace_io(...)` (langfuse v4 API — NOT `start_as_current_span`/`update_trace`, those are v3/older docs). `trace_url` lands on the RCA row. |
| End-to-end run | ✓ `uv run python -m agents.cli investigate --metric revenue --from 2026-07-02T14:00 --to 2026-07-02T17:00 --direction spike --observed 82.85 --baseline 77.30` — full pipeline, ~70s, correctly named `requests` as the guilty factor with `ad_format`/`app_id` segment breakdowns; guardrail caught 2 Writer-derived (non-quoted) percentages on one run and correctly downgraded confidence. Trace viewable at the printed `cloud.langfuse.com` URL. `rca_results` + `index.md` both confirmed appending correctly, including for a failed run (mid-debug). |
| `agents/api.py` | ✓ **Phase 4 done.** FastAPI app, `POST /alert` on port 9100 — accepts the `Alert` schema directly as the JSON body (HyperDX payload shape still not agreed — see External blockers), builds the same `Alert`, calls the same `run_rca()`, writes via `deliver()`. Synchronous (~60-90s per call — caller needs a generous HTTP read timeout). `?force=true` re-runs an `alert_id` that was already processed; without it, a repeat returns **409**. Malformed body → **422** (FastAPI/Pydantic validation, automatic). `GET /health` for liveness checks. Run: `uv run uvicorn agents.api:app --host 0.0.0.0 --port 9100`. |
| Webhook end-to-end test | ✓ Started the server, `curl POST /alert` with a real eCPM-drop window → 200 OK, full RCA JSON back, row landed in `rca_results` with `alert_id=webhook_test_001`. Confirmed 409 on repeat and 422 on a garbage body. |

## Credentials / environment gotchas

- Secrets live in **`.env`** (gitignored). `.env.example` is placeholders only
  — NEVER put real values there, it gets committed to the public repo.
- ClickHouse creds in `.env` are **verified working** (server 26.2.1).
  Password was pasted in a chat once — rotate after the event.
- **ANTHROPIC_API_KEY in .env is BROKEN for the API**: it's an OAuth token
  (`sk-ant-oat01-…`) and returns 401. Get a real key (`sk-ant-api03-…`) from
  console.anthropic.com before Phase 3.
- Langfuse keys: still empty — needed for Phase 3 tracing (cloud.langfuse.com
  free tier, create project → copy public+secret keys).

## External blockers / team facts

- **Database has NO event data yet** — only `rca_results`. P1 teammate hasn't
  loaded `ad_events` / built `events_enriched`. Phase 2 tools need data.
  Fallback: load `ad_events` from
  `click-a-thon-2026/InMobi/data/ad_events.parquet` (103MB, real file) and
  test 4/6 tools on `ad_format` only.
- **Dimension CSVs are broken git-LFS pointers** (objects 404 on GitHub —
  verified via LFS batch API). Without `geo_device.csv` there is no
  region/device drill-down. Recover from the original organizer package or
  a teammate's machine; recommit WITHOUT LFS (files are tiny).
- Alert webhook payload from part 2 not yet agreed — send teammate the
  `Alert` schema from `agents/schemas.py` and ask them to fire a HyperDX
  test alert at webhook.site to capture the real payload shape.

## Next steps (Phase 5 — outputs polish + team integration, ≈1.5h)

POC (hour 8–10 target) is DONE and both entry doors work — CLI and the
FastAPI webhook (`POST /alert` on :9100) both drive the same `run_rca()`.

Remaining before hand-off to part 4/5:
- **Still blocked on the real HyperDX payload shape.** `POST /alert`
  currently accepts the `Alert` schema directly as its JSON body — send the
  part-2 teammate `agents/schemas.py::Alert` and get a captured
  webhook.site payload; then write a small adapter function
  (`hyperdx_payload -> Alert`) in `agents/api.py` ahead of the existing
  logic. Nothing downstream of `Alert` construction needs to change.
- **Opportunistic:** recover the LFS-broken dimension CSVs (`geo_device.csv`
  etc.) so `DIMENSIONS` in `agents/tools.py` can grow past the current 4
  raw-column dims to the full 9-dim plan (region/device/publisher_tier
  drill-downs currently unavailable).
- Confirm `index.md` format is what the part-5 chat agent expects to parse.
- Decide whether `/alert` should run the pipeline synchronously (current
  behavior, simplest) or kick off a background task and return immediately
  — only matters if HyperDX's webhook caller has a short timeout.

Test windows with data (2026-06-01 → 2026-07-05): use e.g.
`2026-07-02T14:00 → 17:00` (all 4 baseline weeks in range); the old June 18
example only has 2 baseline weeks in-range (tools still work — median of 2).

Then Phase 5 (outputs polish + team integration, part 4/5 handoff).

## Done earlier (Phases 2–3 — for reference)

Build `agents/tools.py` with six `@tool` functions (see plan for the full
table + code pattern): `metric_overview`, `factor_decompose`,
`contribution_by_dimension`, `drilldown_filtered`, `seasonality_check`,
`verify_claim`. Rules: dimension allowlist (9 dims), parameterized SQL only,
top-20 rows, errors returned as JSON not raised, executed SQL included in
output. Baseline = same hour-of-day × day-of-week, trailing 4 weeks, median.
Unit-test each tool as a plain function against a known window BEFORE any
agent uses it; cross-check one output by hand in the ClickHouse console.

All of the above shipped and verified — see the Current state table.

## Quick commands

```bash
cd ~/clickathon/clickathon2026
uv run python -m agents.cli investigate --metric revenue \
  --from 2026-06-18T14:00 --to 2026-06-18T17:00 --observed 41200 --baseline 47000
uv run python -c "from agents import db; print(db.q('SELECT count() FROM rca_results'))"
tail -20 index.md
```
