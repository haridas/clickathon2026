"""Scheduler for a finalized-day hourly rollup and out-of-sample forecasts."""

from datetime import date, datetime, timedelta, timezone
import logging

import pandas as pd

from clickhouse_client import ClickHouseClient
from config import SUPPORTED_DIMENSIONS
from .pipeline import AnomalyPipeline, DetectionRequest

LOGGER = logging.getLogger(__name__)


def run_once(days: int, metrics: tuple[str, ...]) -> tuple[date, dict[str, pd.DataFrame], pd.DataFrame] | None:
    repository = ClickHouseClient()
    # If the stream has already started today, today is partial and yesterday is
    # safe. If it has not, the latest event day itself is the latest finalized
    # day. This also makes historical backfill testing behave correctly.
    completed_day = min(repository.latest_event_day(), datetime.now(timezone.utc).date() - timedelta(days=1))
    if not repository.materialize_hourly_rollup(completed_day):
        LOGGER.info("No new completed day to process; skipping model refit.")
        return None
    pipeline = AnomalyPipeline(repository)
    baselines: dict[str, pd.DataFrame] = {}
    for metric in metrics:
        baselines[metric] = pipeline.run(DetectionRequest(metric, SUPPORTED_DIMENSIONS, completed_day, days, True))
    revenue = baselines.get("revenue")
    contributors = (
        repository.get_revenue_contributors(str(revenue.iloc[0].model_run_id))
        if revenue is not None and not revenue.empty and revenue.is_anomaly.eq(1).any()
        else pd.DataFrame()
    )
    return completed_day, baselines, contributors
