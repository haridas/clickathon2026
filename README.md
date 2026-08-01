# InMobi RCA — daily Prophet baseline pipeline

Prophet detects an unexpected movement and its dimension. ClickHouse performs metric attribution, persists every forecast, and powers HyperDX alerts. The resulting alert is auditable: each row contains actual value, expected value, bounds, residual, normalized deviation, model run, and fit time.

```text
ad_events → metrics_daily (long rollup) → Prophet per daily slice
→ metric_baselines → ClickHouse SQL gates → HyperDX alert → RCA workflow
```

## Tables

The source schema lives in the `hackathon` database: `ad_events`, `apps`, `advertisers`, and `geo_device`. `metrics_daily` is a long-format `SummingMergeTree`: one daily row for every dimension bucket (`global`, region, country, device, format, app category, publisher tier, vertical, campaign type). Ratios are derived as `sum(numerator)/sum(denominator)`, never stored or averaged.

`metric_baselines` persists every forecast, including non-anomalies. `z` is `residual / (yhat_upper - yhat)`, allowing cross-series ranking.

## Operations

Materialize a **finalized** UTC day once (a `SummingMergeTree` will double-count if you insert the same day twice):

```powershell
python main.py --materialize-day 2026-07-31
```

Backfill the existing five weeks before the first model run. The command skips days that already have rollup rows, making a safe restart possible:

```powershell
python main.py --backfill-start 2026-06-01 --backfill-end 2026-07-06
```

Run a daily baseline job after the rollup. It defaults to all supported dimensions and a 35-day lookback:

```powershell
python main.py --metric revenue --days 35
python main.py --metric ctr --days 35 --dry-run
```

Use a scheduler to run the two commands after the UTC day closes. Initial historical backfill must insert each day exactly once.

## Real-time demo data and scheduling

The event generator continuously inserts realistic rows into `hackathon.ad_events`; choose a scenario to demonstrate a known incident. Event-time can advance faster than wall-clock time for a demo.

```powershell
# Normal traffic, one 5,000-event batch every ten seconds
python realtime_generator.py

# Inject a controllable revenue incident
python realtime_generator.py --scenario revenue_drop --events-per-batch 10000 --interval-seconds 5

# Validate one insert without keeping a process running
python realtime_generator.py --scenario fill_rate_drop --once
```

Run the finalized-day detection workflow continuously. Its frequency is configurable; it is safe to poll hourly because the rollup skips an already materialized day. The scheduler uses the latest event timestamp (data-time), so it also works when the demo generator advances time faster than wall-clock time.

```powershell
python scheduler.py --check-every-seconds 3600
python scheduler.py --metric revenue --metric fill_rate --once
```

This is **daily** anomaly detection: a completed UTC day is assessed after it closes. The generator is real-time, but a true fifteen-minute/hourly alert requires a separate intraday rollup and model; do not treat partial-day daily metrics as an incident signal.

## Prophet configuration

The model is daily-grain: no intraday or yearly seasonality, weekly seasonality enabled, multiplicative mode, `changepoint_prior_scale=0.01`, and a 99% interval. Five weekly cycles are a baseline, not mature training data; downgrade alerts where a same-weekday trailing-three-week median disagrees with Prophet.

## HyperDX alert query

This query is the starting alert gate. Tune `global_revenue` and add parent/child suppression in the RCA layer:

```sql
WITH global_revenue AS (
  SELECT y FROM metric_baselines
  WHERE day = today() - 1 AND metric = 'revenue' AND dim_name = 'global' AND dim_value = 'all'
  ORDER BY fitted_at DESC LIMIT 1
)
SELECT dim_name, dim_value, metric, y, yhat, z, residual AS revenue_at_risk
FROM metric_baselines
WHERE day = today() - 1
  AND is_anomaly = 1
  AND abs(residual) >= (SELECT y * 0.005 FROM global_revenue)
ORDER BY abs(z) * abs(residual) DESC
LIMIT 10;
```

For rate metrics, gate alerts on a minimum daily request volume (for example `requests >= 5000`) by joining `metrics_daily` for the same day/dimension.
