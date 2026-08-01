"""One-time, idempotent loader for the canonical hackathon source tables."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from clickhouse_client import ClickHouseClient

DIMENSION_FILES = {"apps": "apps.csv", "advertisers": "advertisers.csv", "geo_device": "geo_device.csv"}
EVENT_COLUMNS = ["event_time", "app_id", "geo_device_id", "advertiser_id", "ad_format", "is_filled", "is_impression", "is_click", "revenue"]
RESET_TABLES = ("metric_baselines_hourly", "metrics_hourly", "metric_baselines", "metrics_daily", "ad_events", "apps", "advertisers", "geo_device")


def table_count(client, table: str) -> int:
    return int(client.query_df(f"SELECT count() AS n FROM {table}").iloc[0]["n"])


def load_if_empty(client, table: str, frame: pd.DataFrame) -> int:
    if table_count(client, table):
        print(f"{table}: already contains data; skipped.")
        return 0
    client.insert_df(table, frame)
    print(f"{table}: inserted {len(frame):,} rows.")
    return len(frame)


def reset_hackathon(client: ClickHouseClient) -> None:
    """Clear only the known hackathon tables before a controlled local reload."""
    client.ensure_tables()
    existing = set(client.client.query_df(
        "SELECT name FROM system.tables WHERE database = {database:String}",
        parameters={"database": "hackathon"},
    )["name"])
    for table in RESET_TABLES:
        if table not in existing:
            continue
        client.client.command(f"TRUNCATE TABLE hackathon.{table}")
    print("Cleared hackathon source, rollup, and baseline tables.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Load raw InMobi dataset into hackathon ClickHouse tables.")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--chunk-size", type=int, default=250_000)
    parser.add_argument("--reset", action="store_true", help="Destructively clear only known hackathon tables before loading local source files.")
    args = parser.parse_args()
    if args.chunk_size < 1:
        raise ValueError("--chunk-size must be positive.")
    client = ClickHouseClient()
    client.initialize_source_schema()
    if args.reset:
        reset_hackathon(client)
    for table, filename in DIMENSION_FILES.items():
        load_if_empty(client.client, table, pd.read_csv(args.data_dir / filename))
    if table_count(client.client, "ad_events"):
        print("ad_events: already contains data; skipped.")
        return
    event_file = args.data_dir / "ad_events.parquet"
    inserted = 0
    parquet = pq.ParquetFile(event_file)
    for record_batch in parquet.iter_batches(batch_size=args.chunk_size, columns=EVENT_COLUMNS):
        batch = record_batch.to_pandas()
        client.client.insert_df("ad_events", batch)
        inserted += len(batch)
        print(f"ad_events: inserted {inserted:,} rows.")


if __name__ == "__main__":
    main()
