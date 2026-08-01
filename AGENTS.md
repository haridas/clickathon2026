# Project guide

Keep context small: read this file, `README.md`, then only the module relevant to the task.

## Architecture

`hackathon.ad_events` is the raw fact table. `metrics_daily` is an idempotent daily long-format rollup. `metric_baselines` stores all Prophet forecasts and anomaly flags. HyperDX consumes the SQL in `alerts/`.

## Code map

- `clickhouse_client.py`: schema, ingestion, rollup and ClickHouse reads/writes.
- `anomaly_detection/`: Prophet preparation, scoring, storage and pipeline.
- `realtime_generator.py`: controllable synthetic event stream.
- `scheduler.py`: only refits after a newly completed event day.
- `langfuse_usage/`: local Langfuse health and token reporting.

## Guardrails

- Use named ClickHouse parameters; dimension names must remain allow-listed.
- Never rerun a daily rollup manually without its duplicate guard.
- Ratios are calculated as totals divided by totals; never average ratios.
- Daily Prophet needs at least 28 complete days; default lookback is 35.
- Do not print `.env` values or commit `data/`.

## Useful commands

`python main.py --metric revenue --days 35 --dry-run`

`python scheduler.py --once --metric revenue`

`python alerts/check_alerts.py --day 2026-07-05`

`python langfuse_usage/check_docker.py`
