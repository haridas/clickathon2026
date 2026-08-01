"""ClickHouse repository: rollups, daily model input, and auditable baselines."""

from __future__ import annotations

import logging
from datetime import date
from typing import Iterable

import clickhouse_connect
import pandas as pd

from config import BASELINES_TABLE, CLICKHOUSE, METRICS, METRICS_DAILY_TABLE, ROLLUP_DIMENSIONS

LOGGER = logging.getLogger(__name__)


class ClickHouseClient:
    def __init__(self) -> None:
        missing = [key for key in ("host", "username", "password") if not CLICKHOUSE[key]]
        if missing:
            raise ValueError(f"Missing ClickHouse configuration: {', '.join(missing)}")
        self.client = clickhouse_connect.get_client(**CLICKHOUSE)
        LOGGER.info("Connected to ClickHouse database '%s'", CLICKHOUSE["database"])

    @staticmethod
    def initialize_source_schema() -> None:
        """Create the canonical hackathon database and raw event tables."""
        client = clickhouse_connect.get_client(**{**CLICKHOUSE, "database": "default"})
        client.command("CREATE DATABASE IF NOT EXISTS hackathon")
        client.command("""CREATE TABLE IF NOT EXISTS hackathon.apps
            (app_id String, category LowCardinality(String), publisher_tier LowCardinality(String))
            ENGINE = MergeTree ORDER BY app_id""")
        client.command("""CREATE TABLE IF NOT EXISTS hackathon.advertisers
            (advertiser_id String, vertical LowCardinality(String), campaign_type LowCardinality(String))
            ENGINE = MergeTree ORDER BY advertiser_id""")
        client.command("""CREATE TABLE IF NOT EXISTS hackathon.geo_device
            (geo_device_id String, region LowCardinality(String), country LowCardinality(String),
             device_model LowCardinality(String), os_version LowCardinality(String))
            ENGINE = MergeTree ORDER BY geo_device_id""")
        client.command("""CREATE TABLE IF NOT EXISTS hackathon.ad_events
            (event_time DateTime64(3, 'UTC'), app_id String, geo_device_id String,
             advertiser_id String, ad_format LowCardinality(String), is_filled UInt8,
             is_impression UInt8, is_click UInt8, revenue Float64)
            ENGINE = MergeTree PARTITION BY toYYYYMM(event_time) ORDER BY event_time""")
        LOGGER.info("Canonical source schema is ready in database 'hackathon'.")

    def ensure_tables(self) -> None:
        self.client.command(f"""
            CREATE TABLE IF NOT EXISTS {METRICS_DAILY_TABLE} (
                day Date, dim_name LowCardinality(String), dim_value LowCardinality(String),
                requests UInt64, fills UInt64, impressions UInt64, clicks UInt64, revenue Float64
            ) ENGINE = SummingMergeTree ORDER BY (dim_name, dim_value, day)
        """)
        self.client.command(f"""
            CREATE TABLE IF NOT EXISTS {BASELINES_TABLE} (
                day Date, dim_name LowCardinality(String), dim_value LowCardinality(String),
                metric LowCardinality(String), y Float64, yhat Float64, yhat_lower Float64,
                yhat_upper Float64, residual Float64, z Float64, is_anomaly UInt8,
                model_run_id UUID, fitted_at DateTime
            ) ENGINE = MergeTree ORDER BY (metric, dim_name, dim_value, day, model_run_id)
        """)

    def materialize_daily_rollup(self, day: date) -> bool:
        """Insert one finalized UTC day once; protects SummingMergeTree from duplicates."""
        self.ensure_tables()
        existing = self.client.query_df(
            f"SELECT count() AS rows FROM {METRICS_DAILY_TABLE} WHERE day = {{day:Date}}",
            parameters={"day": day},
        )
        if int(existing.iloc[0]["rows"]) > 0:
            LOGGER.warning("Rollup for %s already exists; skipped to prevent double-counting.", day)
            return False
        dimensions = ", ".join(f"('{name}', {value})" for name, value in ROLLUP_DIMENSIONS.items())
        self.client.command(f"""
            INSERT INTO {METRICS_DAILY_TABLE}
            SELECT toDate(e.event_time) AS day, d.1 AS dim_name, d.2 AS dim_value,
                   count() AS requests, sum(e.is_filled) AS fills,
                   sum(e.is_impression) AS impressions, sum(e.is_click) AS clicks,
                   sum(e.revenue) AS revenue
            FROM ad_events AS e
            LEFT JOIN apps AS a ON e.app_id = a.app_id
            LEFT JOIN geo_device AS g ON e.geo_device_id = g.geo_device_id
            LEFT JOIN advertisers AS ad ON e.advertiser_id = ad.advertiser_id
            ARRAY JOIN [{dimensions}] AS d
            WHERE toDate(e.event_time) = {{day:Date}}
            GROUP BY day, dim_name, dim_value
        """, parameters={"day": day})
        LOGGER.info("Materialized daily rollup for %s", day)
        return True

    def latest_event_day(self) -> date:
        """Return the latest event day; supports both live and accelerated demo clocks."""
        latest = self.client.query_df("SELECT max(toDate(event_time)) AS day FROM ad_events").iloc[0]["day"]
        if pd.isna(latest):
            raise ValueError("ad_events is empty; cannot schedule a detection run.")
        return pd.Timestamp(latest).date()

    def get_daily_series(self, metric: str, dimensions: Iterable[str], start: date, end: date) -> pd.DataFrame:
        if metric not in METRICS:
            raise ValueError(f"Unsupported metric '{metric}'.")
        dimensions = tuple(dimensions)
        invalid = set(dimensions) - set(ROLLUP_DIMENSIONS)
        if invalid:
            raise ValueError(f"Unsupported dimensions: {', '.join(sorted(invalid))}")
        query = f"""
            SELECT m.day AS ds, m.dim_name, m.dim_value, {METRICS[metric].expression} AS y,
                   sum(m.requests) AS requests, sum(m.revenue) AS revenue
            FROM {METRICS_DAILY_TABLE} AS m
            WHERE m.day >= {{start:Date}} AND m.day < {{end:Date}} AND m.dim_name IN {{dimensions:Array(String)}}
            GROUP BY ds, m.dim_name, m.dim_value ORDER BY m.dim_name, m.dim_value, ds
        """
        result = self.client.query_df(query, parameters={"start": start, "end": end, "dimensions": list(dimensions)})
        result["ds"] = pd.to_datetime(result["ds"])
        return result

    def save_baselines(self, baselines: pd.DataFrame) -> int:
        if baselines.empty:
            return 0
        self.ensure_tables()
        self.client.insert_df(BASELINES_TABLE, baselines)
        return len(baselines)
