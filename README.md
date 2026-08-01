# Revenue anomaly detection and root-cause analysis

This project detects **material, sustained global revenue anomalies** in ad-event data and, only when one is confirmed, identifies the segments that best explain the revenue gap. It uses ClickHouse for aggregation and storage, Prophet for the hourly forecast.

```text
ad_events -> metrics_hourly -> global revenue Prophet forecast
                                  |
                         confirmed incident?
                           | no        | yes
                           v           v
                       store forecast  forecast one-level segments
                                               |
                                               v
                         revenue_attribution_hourly -> HyperDX alert
```

## What it detects

Each completed UTC hour has an actual revenue value and a Prophet forecast. A global revenue incident requires all of the following:

- at least 21 prior complete days (three weekly cycles) of training data;
- actual revenue outside Prophet's 99% prediction interval;
- an absolute gap of at least 10% of expected revenue;
- at least two consecutive anomalous hours in the same direction; and
- at least 250 requests in each hour.

For example, if revenue is expected to be 100,000 with a lower 99% bound of 90,000, 82,000 for three consecutive hours is an alert: it is below the expected range, 18% below forecast, and persistent. A single low hour is recorded but does not page anyone.

The detector trains only on data before the day it scores. Historical **persistent** revenue shocks are replaced in the training copy by their same-hour-of-week median so that Prophet does not learn an outage as normal behaviour. The scored target values are never changed.

## Segment root cause

When global revenue is confirmed anomalous, the application forecasts the supported one-level segments: region, country, device model, OS version, ad format, category, publisher tier, advertiser vertical, and campaign type. For each target hour it calculates:

```text
global revenue residual      = actual global revenue - expected global revenue
segment contribution share   = segment residual / global residual
```

The top three same-direction segments are retained **within each dimension family**. For example, `category=finance` might explain 30% of the gap and `os_version=Android 15` 20%. Country, category, and OS overlap, so their percentages are separate diagnostic views and must never be added together.

This is segment attribution, not proof of business causality. To state whether a loss was caused by traffic, fill, rendering, or price, examine the companion metrics using:

```text
revenue = requests x fill_rate x render_rate x eCPM / 1000
```

## Required files

- `clickhouse_client.py` — source schema, hourly rollup, and ClickHouse reads/writes.
- `config.py` — supported metrics and dimensions.
- `load_data.py` — loads the supplied source data.
- `realtime_generator.py` — controlled test-event generator.
- `anomaly_detection/` — Prophet preparation, forecast, scoring, attribution, scheduler, and backtest.
- `alerts/` — ClickHouse candidate query and HyperDX publisher/configuration.

`data/` contains the supplied input data. Do not commit it or `.env`.

## Setup and historical backfill

1. Create a virtual environment, install dependencies, and set the ClickHouse connection values in `.env` from `.env.example`. Never commit `.env`.

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

2. Load the provided data. `--reset` truncates the project tables first, so use it only when a reload is intended.

   ```powershell
   python load_data.py --reset
   ```

3. Create the hourly rollups once for historical data. The end date is exclusive.

   ```powershell
   python -m anomaly_detection.cli --backfill-start 2026-06-01 --backfill-end 2026-07-06
   ```

## Run revenue detection

Use a dry run to see confirmed anomaly hours without writing model rows:

```powershell
python -m anomaly_detection.cli --metric revenue --target-day 2026-07-05 --days 35 --dry-run
```

Run without `--dry-run` to save forecasts, confirmed flags, and (when required) segment attribution:

```powershell
python -m anomaly_detection.cli --metric revenue --target-day 2026-07-05 --days 35
python alerts/check_alerts.py --day 2026-07-05
```

The primary output tables are:

- `metrics_hourly` — raw hourly totals by dimension;
- `metric_baselines_hourly` — global and, during confirmed revenue incidents, segment forecasts; and
- `revenue_attribution_hourly` — ranked segment residual shares for confirmed global revenue hours.

## Evaluate the model

Use rolling out-of-sample evaluation. Every test day is forecast only from its preceding training window; no future observations enter training.

```powershell
python -m anomaly_detection.backtest --metric revenue --train-days 21 --test-start 2026-06-22 --test-end 2026-07-06
```

Review MAE and RMSE for forecast error, interval coverage against the configured 99% interval, and the confirmed anomaly hours. The first 21 days are cold-start training only and cannot be assessed with this seasonal model.

## Automation and HyperDX

After incoming data for a UTC day is final, score the previous day once:

```powershell
python -m anomaly_detection.runner --once --metric revenue
```

For continuous polling:

```powershell
python -m anomaly_detection.runner --check-every-seconds 3600 --metric revenue
```

To publish confirmed candidates to HyperDX, add `HYPERDX_API_KEY` and `OTEL_SERVICE_NAME` to your deployment secret store, then schedule:

```powershell
opentelemetry-instrument python alerts/daily_workflow.py --metric revenue
```

Follow [the HyperDX configuration guide](alerts/hyperdx/README.md) to create the saved-search alert. The publisher emits only confirmed rows from `alerts/anomaly_alert.sql`.

## Controlled test data

The generator inserts future event-time batches; it does not make a completed historical day anomalous by itself. Generate enough event time to close a day, then run the scheduler:

```powershell
python realtime_generator.py --scenario revenue_drop --events-per-batch 10000 --simulated-seconds-per-batch 3600
python -m anomaly_detection.runner --once --metric revenue
```

Use it after normal history has established the 21-day minimum. For a realistic test, first generate normal data and then run the revenue-drop scenario for several consecutive simulated hours.
