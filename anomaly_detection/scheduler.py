"""Scheduler for a finalized-day hourly rollup and out-of-sample forecasts."""

from datetime import timedelta
import logging

from clickhouse_client import ClickHouseClient
from config import SUPPORTED_DIMENSIONS
from .pipeline import AnomalyPipeline, DetectionRequest

LOGGER = logging.getLogger(__name__)


def run_once(days: int, metrics: tuple[str, ...]) -> None:
    repository = ClickHouseClient()
    completed_day = repository.latest_event_day() - timedelta(days=1)
    if not repository.materialize_hourly_rollup(completed_day):
        LOGGER.info("No new completed day to process; skipping model refit.")
        return
    pipeline = AnomalyPipeline(repository)
    for metric in metrics:
        pipeline.run(DetectionRequest(metric, SUPPORTED_DIMENSIONS, completed_day, days, True))
