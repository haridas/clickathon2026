"""Orchestration from ClickHouse daily metrics to persisted Prophet baselines."""

from dataclasses import dataclass
from datetime import date
import logging
from uuid import uuid4

import pandas as pd

from clickhouse_client import ClickHouseClient
from config import MIN_HISTORY_DAYS
from .detector import ProphetAnomalyDetector
from .preprocessing import prepare_daily_series
from .storage import to_baseline_rows

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DetectionRequest:
    metric: str
    dimensions: tuple[str, ...]
    start: date
    end: date
    persist: bool = True


class AnomalyPipeline:
    def __init__(self, repository: ClickHouseClient | None = None) -> None:
        self.repository = repository or ClickHouseClient()
        self.detector = ProphetAnomalyDetector()

    def run(self, request: DetectionRequest) -> pd.DataFrame:
        raw = self.repository.get_daily_series(request.metric, request.dimensions, request.start, request.end)
        run_id, output, skipped = uuid4(), [], 0
        for (dim_name, dim_value), group in raw.groupby(["dim_name", "dim_value"], dropna=False):
            history = prepare_daily_series(group)
            if len(history) < MIN_HISTORY_DAYS:
                skipped += 1
                continue
            output.append(to_baseline_rows(self.detector.score(history), request.metric, dim_name, dim_value, run_id))
        baselines = pd.concat(output, ignore_index=True) if output else pd.DataFrame()
        saved = self.repository.save_baselines(baselines) if request.persist else 0
        LOGGER.info("Baseline run=%s series=%d skipped=%d rows=%d saved=%d", run_id, len(output), skipped, len(baselines), saved)
        return baselines
