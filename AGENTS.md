# Project guide

Keep context small: read this file, `README.md`, then only the module relevant to the task.

## Architecture

`hackathon.ad_events` is the raw fact table. `metrics_hourly` is the idempotent hourly long-format rollup. `metric_baselines_hourly` stores out-of-sample Prophet forecasts and anomaly flags. HyperDX consumes the SQL in `alerts/`.

## Code map

- `clickhouse_client.py`: schema, ingestion, rollup and ClickHouse reads/writes.
- `anomaly_detection/`: Prophet preparation, scoring, storage and pipeline.
- `realtime_generator.py`: controllable synthetic event stream.
- `anomaly_detection/runner.py`: only refits after a newly completed event day.

## Guardrails

- Use named ClickHouse parameters; dimension names must remain allow-listed.
- Never rerun a daily rollup manually without its duplicate guard.
- Ratios are calculated as totals divided by totals; never average ratios.
- Hourly Prophet needs at least 21 complete days; default lookback is 35.
- Do not print `.env` values or commit `data/`.

## Useful commands

`python -m anomaly_detection.cli --metric revenue --target-day 2026-07-05 --days 35 --dry-run`

`python -m anomaly_detection.runner --once --metric revenue`

`python alerts/check_alerts.py --day 2026-07-05`
