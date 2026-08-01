# Agents — InMobi Root-Cause Analyst

This defines the agent architecture for the "alert to answer" system and the
constraints each agent must follow. It assumes `PROBLEM_STATEMENT.md` and
`metrics_glossary.md` — read those first. `alerting/` holds the detection-layer
implementation this file wires together (`prophet_forecast.py`, `schema.sql`).

## The one non-negotiable rule

**The LLM narrates. It never computes, never queries raw events, and never
states a number that wasn't already resolved by a ClickHouse query before the
narration call runs.** This isn't a style preference — three of the five
judged criteria hinge on it directly:

> "Trustworthy... *Consider: let deterministic code do the analysis and use
> the LLM only to narrate.*" — What "great" looks like

> "Explanation trustworthiness — every number in the diagnosis must be
> reproducible from the data. A single fabricated figure costs more than a
> missed anomaly." — How you will be evaluated

> "Analytical depth in ClickHouse — the drill-down should live in queries,
> not in the LLM. Judges will look at whether ClickHouse is doing the real
> work." — How you will be evaluated

> "A system that streams raw events into an LLM will be slow, expensive, and
> prone to inventing numbers." — Notes & boundaries

If an agent's job could be done by pointing an LLM at raw rows, that's a sign
the job belongs to ClickHouse instead.

## Model inventory — why this count and no more

Every computational artifact in the pipeline, and why it exists. Kept
deliberately small: per-segment ML models don't scale (thousands of
app × device × geo × advertiser combinations) and buy nothing a window
function doesn't already give you, per "explainability and trustworthiness
matter more than sophistication."

| # | Model / method | Type | Scope | Verified in the trend report |
|---|---|---|---|---|
| 1 | Trailing same-time-of-day baseline | deterministic (window fn) | any metric, 15-min grain, fast path | caught the Jun 21 drop 45 min after midnight |
| 2 | `seriesOutliersDetectTukey` | ClickHouse-native fn | any metric, daily grain | 0 false positives on requests |
| 3 | `seriesDecomposeSTL` | ClickHouse-native fn | any metric, tunable period (7 daily / 168 hourly) | isolated both planted anomalies cleanly |
| 4 | Prophet — revenue, daily | trained model | 1 headline metric | 1/35 days flagged, exactly Jun 21 |
| 5 | Prophet — fill_rate, daily | trained model | 1 headline metric | 3/35 days flagged, exactly Jun 23–25 |

**Total: 3 deterministic techniques + 2 trained models = 5, and exactly 1 LLM,
called once per detected incident.** Not one model per segment, not one LLM
call per candidate hypothesis — both would fail "Fast" (seconds, not a
model-training loop) and inflate hallucination surface area for no accuracy
gain the trend report's testing didn't already show was unnecessary.

The drill-down/attribution step below is not a model at all — it's the
revenue identity from `metrics_glossary.md` (`Revenue ≈ Requests × Fill rate ×
eCPM/1000`) walked as a ranked GROUP BY, which is exactly the "simple
baselines and contribution analysis" the problem statement calls acceptable
over ML.

## Agents

### 1. Detector — deterministic, no LLM

Runs the model inventory above on a schedule or on-demand. Input: a metric
name and granularity. Output: a boolean (deviated / normal) plus the raw
numbers backing it (actual, baseline, band, method used). This is pure
ClickHouse — see `alerting/prophet_forecast.py`'s `alert_query()` for the
pattern (even the Prophet-backed check is a ClickHouse query, not a Python
comparison).

### 2. Attributor — deterministic, no LLM

Given a fired alert (metric + time window), walks the revenue identity and
the dimension list from `metrics_glossary.md` (`ad_format`, `category`,
`publisher_tier`, `vertical`, `campaign_type`, `region`, `country`,
`device_model`, `os_version`):

1. Slice the deviating metric by each dimension for the incident window vs.
   the same like-for-like baseline the Detector used.
2. Rank slices by contribution to the deviation (e.g. share of the total
   delta), not by raw magnitude — a segment that's 90% of traffic and 90% of
   the drop isn't "responsible," it's just big.
3. Emit two lists: **found** (segments whose deviation is statistically
   significant and explains most of the gap) and **ruled out** (dimensions
   checked with no significant deviation — the bonus honesty criterion).

Output is a JSON `findings` object, e.g.:

```json
{
  "metric": "requests",
  "window": "2026-06-21",
  "baseline": 227015,
  "actual": 126052,
  "deviation_pct": -44.5,
  "detector": "trailing-baseline-15min, fired 00:45",
  "found": [
    {"dimension": "region", "segment": "NAM", "contribution_pct": 61.2, "actual": 41210, "baseline": 106300}
  ],
  "ruled_out": [
    {"dimension": "ad_format", "note": "deviation uniform across all 5 formats, no single format implicated"},
    {"dimension": "publisher_tier", "note": "fill rate normal across tier_1/2/3"}
  ]
}
```

Every field here must trace back to a specific query result — this JSON *is*
the trace artifact a judge should be able to open and re-run.

### 3. Narrator — the only agent allowed an LLM, exactly one call per incident

Receives the `findings` JSON above and nothing else — no table access, no
tool calls, no raw events. Its only job is turning structured numbers into
the plain-language diagnosis from the suggested demo:

> *"Revenue fell 12%, driven almost entirely by a drop in fill rate for
> Device X in Region North. Request volume and CTR were normal and ruled
> out."*

System prompt sketch (adapt, don't skip the constraint):

```
You are writing a one-paragraph incident diagnosis for an ad-tech dashboard.

You will receive a JSON object called `findings`. Every number in your
response must appear verbatim in `findings` — do not calculate, round
differently, or infer a number that isn't already there. If a claim would
need a number you don't have, drop the claim instead of estimating it.

Structure: (1) what moved and by how much, (2) which segment(s) explain it,
citing their contribution, (3) what you checked and ruled out, from
`findings.ruled_out`. Keep it to 3-4 sentences.
```

**Recommended safeguard (not a model, a validation function):** before
displaying the Narrator's output, regex-extract every number it wrote and
confirm each one appears in `findings`. Reject and retry once if not. Given
"a single fabricated figure costs more than a missed anomaly," this costs one
string comparison and removes the single largest scoring risk in the whole
system.

## Where this lands on the alert side

The alerting tiers in the trend report map directly onto Detector (#1 above)
— Tier 1/2 are the deterministic techniques, Tier 3 is the two Prophet
models. **None of the three tiers involve an LLM.** The LLM only enters after
the Attributor has already produced a `findings` object — i.e., after an
alert has fired *and* been localized to a segment. A metric moving inside
its normal band never reaches the Narrator at all; there's nothing to
narrate, and no cost in calling one.

## Traceability

Every Detector query, Attributor query, and Narrator call should be a
distinct span in Langfuse (or ClickStack), tagged with the incident ID, so
"a judge should be able to open your traces and follow the investigation:
what was checked, in what order, and why" is satisfied by construction, not
by a screenshot after the fact. The unseen-incident submission's trace is
worth "significant weight" — **no trace, no credit** — so this isn't optional
polish.
