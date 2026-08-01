# Agentic RCA for Ad Platform Metrics

**From alert to answer.** A metric moves → the system investigates itself → a plain-language
diagnosis where every number is computed in ClickHouse, not written by an LLM.

## Principle

> **ClickHouse computes. The LLM narrates.**

Every figure in a diagnosis traces back to a SQL query we can replay. The agent chooses
*what to ask*; it never invents a number. This is the single design rule everything below follows.

## Pipeline

| # | Stage | What it does |
|---|-------|--------------|
| 1 | **Ingest** | Load `ad_events` (9M) + 3 dimension tables into ClickHouse. Materialized views roll up hourly metrics per dimension. Surface in ClickStack. |
| 2 | **Detect** | Daily rollups per dimension → **Prophet** forecasts each series' expected value and interval. Anomaly = actual outside the band. Seasonality is modelled, not thresholded. ([details](#appendix--detection-layer)) |
| 3 | **Trigger** | Alert fires a webhook to the RCA agent with `alert_id`, metric, window, and observed vs. expected. |
| 4 | **Decompose** | Walk the revenue identity in SQL: `Revenue = Requests × Fill rate × Render rate × eCPM/1000`. Which *factor* moved? |
| 5 | **Localize** | Drill the guilty factor by dimension (app, geo, device, OS, advertiser, format) using contribution analysis. Which *segment* moved? Recurse into the top contributors. |
| 6 | **Rule out** | Explicitly test and clear the alternatives: seasonality, volume shift, sibling metrics, adjacent segments. Record the cleared checks as evidence. |
| 7 | **Narrate** | LLM writes the diagnosis from the computed evidence table only. Sub-agents review for unsupported claims and render a clean HTML report. |
| 8 | **Publish** | Findings written back to ClickHouse against `alert_id`. Slack notification links to LibreChat, pre-loaded with the RCA for follow-up questions. |
| 9 | **Digest** | Daily agent summarizes trends, open incidents, and estimated revenue impact. |

## Deliberate design choices

- **Seasonality-aware baseline over static thresholds.** Weekends are genuinely lower;
  a flat threshold cries wolf on every Saturday. At least one planted movement is pure
  seasonality and must be *ruled out*, not alarmed on.
- **Decomposition before drill-down.** Finding the factor first (volume vs. fill vs. price)
  cuts the search space before we start slicing dimensions.
- **Evidence table as the contract.** The narrator receives a structured set of
  `(claim, value, query)` rows. It cannot cite what isn't there.
- **Full trace per investigation** (Langfuse): every query run, in order, with its result and
  the reason it was run. A judge can replay the reasoning. *No trace, no credit.*

## Target output

> Revenue fell **12.4%** on Jun 18 (₹X vs. ₹Y expected). Driven almost entirely by
> **fill rate** on **Galaxy S23 / region EU**, down 31% against its own trailing baseline —
> **68%** of the total gap. Request volume (+2%), CTR (−0.4%), and all other regions were
> checked and are within normal range. Seasonality ruled out: same weekday, prior 3 weeks.

Localized, quantified, and honest about what it cleared.

## Business value

1. **Business on-call** — catches revenue movement the dashboards would show only in hindsight.
2. **Prioritization** — RCAs ranked by revenue at risk, so engineering fixes what costs most.
3. **MTTR** — hours of manual dashboard drilling collapse to seconds.
4. **Opportunity discovery** — the same segment analysis surfaces outperformers, not just failures.

## Built for the unseen incident

The build targets the *investigation loop*, not the anomalies we found while developing.
Nothing in the detection or drill-down is tuned to a known segment or date.

---

# Appendix — Detection Layer

Prophet detects **that** something moved and **where to start looking**.
ClickHouse still does all attribution. Forecasts are written back to ClickHouse, so
every alert is auditable: a judge can query why it fired.

## 1. Daily rollup

One long-format table holds every slice, so adding a dimension never changes the schema.

```sql
CREATE TABLE metrics_daily
(
    day         Date,
    dim_name    LowCardinality(String),   -- 'global', 'region', 'ad_format', ...
    dim_value   LowCardinality(String),
    requests    UInt64,
    fills       UInt64,
    impressions UInt64,
    clicks      UInt64,
    revenue     Float64
)
ENGINE = SummingMergeTree
ORDER BY (dim_name, dim_value, day);
```

Populated in one pass — `ARRAY JOIN` emits each event into every dimension bucket it belongs to:

```sql
INSERT INTO metrics_daily
SELECT
    toDate(e.event_time)  AS day,
    d.1                   AS dim_name,
    d.2                   AS dim_value,
    count()               AS requests,
    sum(e.is_filled)      AS fills,
    sum(e.is_impression)  AS impressions,
    sum(e.is_click)       AS clicks,
    sum(e.revenue)        AS revenue
FROM ad_events e
LEFT JOIN apps        a  USING (app_id)
LEFT JOIN geo_device  g  USING (geo_device_id)
LEFT JOIN advertisers ad USING (advertiser_id)
ARRAY JOIN
[
    ('global',         'all'),
    ('region',         g.region),
    ('country',        g.country),
    ('device_model',   g.device_model),
    ('os_version',     g.os_version),
    ('ad_format',      e.ad_format),
    ('category',       a.category),
    ('publisher_tier', a.publisher_tier),
    ('vertical',       ad.vertical),
    ('campaign_type',  ad.campaign_type)
] AS d
GROUP BY day, dim_name, dim_value;
```

Ratios are **always** derived at query time as `sum/sum` — never stored, never averaged:

```sql
SELECT day,
       fills / requests                     AS fill_rate,
       impressions / fills                  AS render_rate,
       clicks / impressions                 AS ctr,
       revenue / impressions * 1000         AS ecpm,
       revenue / requests                   AS rpr
FROM metrics_daily
WHERE dim_name = 'region' AND dim_value = 'EU';
```

## 2. Series to forecast

Single-dimension slices only — the full cross-product is unnecessary because
**Prophet localizes to a dimension, ClickHouse localizes within it.**

| Level | Cardinality |
|---|---|
| global | 1 |
| region / country | 5 + 10 |
| device_model / os_version | ~30 + 8 |
| ad_format | 5 |
| category / publisher_tier | 7 + 3 |
| vertical / campaign_type | 7 + 3 |
| **Total series** | **~80** |

× 6 metrics (revenue, requests, fill_rate, render_rate, ctr, ecpm) ≈ **480 fits**,
each on 35 daily points — a few seconds total, refit on every run.

## 3. Prophet configuration

```python
Prophet(
    yearly_seasonality      = False,   # 5 weeks of data — cannot be estimated
    daily_seasonality       = False,   # daily grain; hour-of-day is aggregated out
    weekly_seasonality      = True,    # the weekend effect, the one that matters
    seasonality_mode        = "multiplicative",
    changepoint_prior_scale = 0.01,    # stiff trend — see below
    interval_width          = 0.99,
)
```

Two settings carry the weight:

- **`changepoint_prior_scale = 0.01`.** At the default `0.05`, Prophet happily absorbs a
  planted step-change as a legitimate trend shift and the anomaly vanishes into `yhat`.
  A stiff trend forces the movement out into the residual, where we can see it.
- **`interval_width = 0.99`.** Detection accuracy is judged on false positives as much as
  misses. A wide band plus the gates in §5 keeps us from crying wolf.

## 4. Persisting the forecast

```sql
CREATE TABLE metric_baselines
(
    day          Date,
    dim_name     LowCardinality(String),
    dim_value    LowCardinality(String),
    metric       LowCardinality(String),
    y            Float64,   -- actual
    yhat         Float64,   -- expected
    yhat_lower   Float64,
    yhat_upper   Float64,
    residual     Float64,   -- y - yhat
    z            Float64,   -- residual / (yhat_upper - yhat)
    is_anomaly   UInt8,
    model_run_id UUID,
    fitted_at    DateTime
)
ENGINE = MergeTree
ORDER BY (metric, dim_name, dim_value, day);
```

`z` normalizes deviation across series of wildly different scale, so a global revenue dip
and a single-country fill-rate dip are directly rankable.

## 5. From anomaly to alert

~480 series × 35 days at a 99% band still yields false positives by construction.
Three gates before anything pages the agent:

1. **Volume floor** — `requests >= 5000` for the day. Thin slices produce meaningless ratios.
2. **Material impact** — deviation must be worth ≥ 0.5% of global daily revenue.
   Ranks incidents by money, which is also how the RCA gets prioritized.
3. **Not merely inherited** — if a child series' deviation is fully explained by its parent
   moving, it isn't a separate incident. Suppress and attach it to the parent.

Survivors are ranked by `|z| × revenue_at_risk` and the top one fires the webhook.

```sql
SELECT dim_name, dim_value, metric, y, yhat, z,
       (y - yhat) AS revenue_at_risk
FROM metric_baselines
WHERE day = {target:Date} AND is_anomaly = 1
ORDER BY abs(z) * abs(revenue_at_risk) DESC
LIMIT 10;
```

## 6. Ruling out seasonality

The decoy in this dataset is a movement that is **pure seasonality**. Prophet handles it for
free and we make the reasoning explicit in the report: if the actual sits inside
`[yhat_lower, yhat_upper]` while the raw week-over-week delta looks alarming, the diagnosis
states *"−18% vs. yesterday is the expected weekend pattern; within forecast interval,
ruled out."* That is a scored outcome, not a non-event.

## Known limitation

35 days gives Prophet only **5 weekly cycles** — the bare minimum for a weekly seasonality
estimate, and it will be somewhat noisy. Mitigation: the Prophet band is the *primary*
signal, cross-checked against a simple same-weekday trailing-3-week median. Where the two
disagree, the alert is downgraded and the disagreement is reported rather than hidden.
