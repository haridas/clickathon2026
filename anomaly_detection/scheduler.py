"""Configurable scheduler for finalized-day rollup and Prophet baseline jobs."""

from datetime import timedelta
import logging

from clickhouse_client import ClickHouseClient
from config import SUPPORTED_DIMENSIONS
from .pipeline import AnomalyPipeline, DetectionRequest

LOGGER = logging.getLogger(__name__)


def run_once(days: int, metrics: tuple[str, ...]) -> None:
    repository = ClickHouseClient()
    completed_day = repository.latest_event_day() - timedelta(days=1)
    end = completed_day + timedelta(days=1)
    if not repository.materialize_daily_rollup(completed_day):
        LOGGER.info("No new completed day to process; skipping model refit.")
        return
    pipeline = AnomalyPipeline(repository)
    for metric in metrics:
        pipeline.run(DetectionRequest(metric, SUPPORTED_DIMENSIONS, end - timedelta(days=days), end, True))
