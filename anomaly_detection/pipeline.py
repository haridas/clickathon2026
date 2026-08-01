"""Out-of-sample hourly forecasting from ClickHouse rollups."""

from dataclasses import dataclass
from datetime import date
import logging
from uuid import uuid4

import numpy as np
import pandas as pd

from clickhouse_client import ClickHouseClient
from .config import (FORECAST_HORIZON_HOURS, MATERIALITY, MIN_HISTORY_HOURS,
                     MIN_HOURLY_REQUESTS, REVENUE_HISTORY_OUTLIER_RATIO,
                     REVENUE_TOP_CONTRIBUTORS)
from .detector import ProphetAnomalyDetector
from .preprocessing import clean_revenue_history, prepare_hourly_series
from .storage import to_baseline_rows

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DetectionRequest:
    metric: str
    dimensions: tuple[str, ...]
    target_day: date
    history_days: int
    persist: bool = True


class AnomalyPipeline:
    def __init__(self, repository: ClickHouseClient | None = None) -> None:
        self.repository = repository or ClickHouseClient()
        self.detector = ProphetAnomalyDetector()

    def _score_group(self, group: pd.DataFrame, request: DetectionRequest, run_id) -> pd.DataFrame | None:
        """Forecast one dimension value, returning its target-day baseline rows."""
        dim_name, dim_value = group.iloc[0][["dim_name", "dim_value"]]
        series = prepare_hourly_series(group)
        history = series.loc[series.ds.dt.date < request.target_day]
        actual = series.loc[series.ds.dt.date == request.target_day]
        target_raw = group.loc[pd.to_datetime(group.ds).dt.date == request.target_day]
        if (len(history) < MIN_HISTORY_HOURS or len(actual) != FORECAST_HORIZON_HOURS
                or len(target_raw) != FORECAST_HORIZON_HOURS or target_raw.requests.min() < MIN_HOURLY_REQUESTS):
            return None
        if request.metric == "revenue":
            history = clean_revenue_history(history, REVENUE_HISTORY_OUTLIER_RATIO)
        scored = self.detector.score(history, actual)
        mode, threshold = MATERIALITY[request.metric]
        material = scored.residual.abs() >= (threshold if mode == "absolute" else scored.yhat.abs() * threshold)
        candidate = scored.is_anomaly.eq(1) & material
        sign = np.sign(scored.residual).replace(0, 1)
        run = (candidate.ne(candidate.shift()) | sign.ne(sign.shift())).cumsum()
        persistent = candidate.groupby(run).transform("sum").ge(2)
        scored["is_anomaly"] = (candidate & persistent).astype("uint8")
        return to_baseline_rows(scored, request.metric, dim_name, dim_value, run_id)

    def _build_revenue_attribution(self, global_rows: pd.DataFrame, child_rows: pd.DataFrame) -> pd.DataFrame:
        """Rank child residuals that explain a confirmed global revenue incident.

        Dimension families overlap (country and device are not additive). Ranking is
        deliberately performed separately within each dim_name.
        """
        incident = global_rows.loc[global_rows.is_anomaly.eq(1), ["bucket", "residual"]].rename(
            columns={"residual": "global_residual"}
        )
        if incident.empty or child_rows.empty:
            return pd.DataFrame()
        attributed = child_rows.loc[child_rows.dim_name.ne("global")].merge(incident, on="bucket", how="inner")
        if attributed.empty:
            return attributed
        attributed["contribution_share"] = attributed.residual / attributed.global_residual.replace(0, np.nan)
        same_direction = attributed.contribution_share.gt(0)
        attributed["contributor_rank"] = (
            attributed.contribution_share.where(same_direction, 0)
            .groupby([attributed.bucket, attributed.dim_name]).rank(method="dense", ascending=False)
            .fillna(0).astype("uint16")
        )
        attributed["is_contributor"] = (
            same_direction & attributed.contributor_rank.le(REVENUE_TOP_CONTRIBUTORS)
        ).astype("uint8")
        return attributed.rename(columns={"y": "actual", "yhat": "expected"})[
            ["bucket", "dim_name", "dim_value", "actual", "expected", "residual", "global_residual",
             "contribution_share", "contributor_rank", "is_contributor", "model_run_id", "fitted_at"]
        ]

    def run(self, request: DetectionRequest) -> pd.DataFrame:
        start = pd.Timestamp(request.target_day) - pd.Timedelta(days=request.history_days)
        end = pd.Timestamp(request.target_day) + pd.Timedelta(days=1)
        raw = self.repository.get_hourly_series(request.metric, request.dimensions, start.date(), end.date())
        run_id, output, skipped = uuid4(), [], 0
        groups = list(raw.groupby(["dim_name", "dim_value"], dropna=False))
        if request.metric == "revenue":
            global_group = next((group for (name, value), group in groups if name == "global" and value == "all"), None)
            if global_group is None:
                raise ValueError("Revenue detection requires the global dimension.")
            global_rows = self._score_group(global_group, request, run_id)
            if global_rows is None:
                raise ValueError("Global revenue has insufficient complete hourly history.")
            output.append(global_rows)
            if global_rows.is_anomaly.eq(1).any():
                for (dim_name, _), group in groups:
                    if dim_name == "global":
                        continue
                    scored = self._score_group(group, request, run_id)
                    if scored is None:
                        skipped += 1
                    else:
                        output.append(scored)
        else:
            for _, group in groups:
                scored = self._score_group(group, request, run_id)
                if scored is None:
                    skipped += 1
                else:
                    output.append(scored)
        baselines = pd.concat(output, ignore_index=True) if output else pd.DataFrame()
        saved = self.repository.save_hourly_baselines(baselines) if request.persist else 0
        attribution = (self._build_revenue_attribution(output[0], baselines) if request.metric == "revenue" and output else pd.DataFrame())
        attributed = self.repository.save_revenue_attribution(attribution) if request.persist else 0
        LOGGER.info("Baseline run=%s series=%d skipped=%d rows=%d saved=%d attributed=%d", run_id, len(output), skipped, len(baselines), saved, attributed)
        return baselines
