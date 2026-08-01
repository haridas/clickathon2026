"""CLI for daily ClickHouse rollups and Prophet forecast baseline runs."""

import argparse
from datetime import date, datetime, timedelta, timezone
import logging

from clickhouse_client import ClickHouseClient
from config import SUPPORTED_DIMENSIONS, SUPPORTED_METRICS
from pipeline import AnomalyPipeline, DetectionRequest


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Daily InMobi RCA baseline pipeline.")
    parser.add_argument("--init-schema", action="store_true", help="Create canonical hackathon source tables, then exit.")
    parser.add_argument("--metric", choices=SUPPORTED_METRICS, default="revenue")
    parser.add_argument("--dimension", action="append", choices=SUPPORTED_DIMENSIONS, default=None, help="Dimension to forecast; repeat for multiple dimensions (all by default).")
    parser.add_argument("--days", type=int, default=35, help="Daily historical lookback (minimum 28).")
    parser.add_argument("--end", type=parse_date, default=datetime.now(timezone.utc).date(), help="Exclusive UTC day, YYYY-MM-DD.")
    materialize = parser.add_mutually_exclusive_group()
    materialize.add_argument("--materialize-day", type=parse_date, help="Insert one finalized raw-event day into metrics_daily, then exit.")
    materialize.add_argument("--backfill-start", type=parse_date, help="First UTC day (inclusive) to backfill into metrics_daily; requires --backfill-end.")
    parser.add_argument("--backfill-end", type=parse_date, help="First UTC day after the backfill range; requires --backfill-start.")
    parser.add_argument("--dry-run", action="store_true", help="Score but do not insert forecast baselines.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.init_schema:
        ClickHouseClient.initialize_source_schema()
        return
    if args.materialize_day:
        ClickHouseClient().materialize_daily_rollup(args.materialize_day)
        return
    if args.backfill_start or args.backfill_end:
        if not args.backfill_start or not args.backfill_end or args.backfill_start >= args.backfill_end:
            raise ValueError("Provide --backfill-start and a later --backfill-end.")
        client = ClickHouseClient()
        current = args.backfill_start
        while current < args.backfill_end:
            client.materialize_daily_rollup(current)
            current += timedelta(days=1)
        return
    if args.days < 28:
        raise ValueError("--days must be at least 28 for weekly seasonality.")
    dimensions = tuple(dict.fromkeys(args.dimension or SUPPORTED_DIMENSIONS))
    baselines = AnomalyPipeline().run(DetectionRequest(args.metric, dimensions, args.end - timedelta(days=args.days), args.end, not args.dry_run))
    if args.dry_run:
        print(baselines.loc[baselines.is_anomaly.eq(1)].to_string(index=False) if not baselines.empty else "No eligible series.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    main()
