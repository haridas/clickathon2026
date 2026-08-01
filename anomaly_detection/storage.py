"""Convert scored forecasts to the hourly baseline storage contract."""

from datetime import datetime, timezone
from uuid import UUID

import pandas as pd


def to_baseline_rows(scored: pd.DataFrame, metric: str, dim_name: str, dim_value: str, model_run_id: UUID) -> pd.DataFrame:
    rows = scored.rename(columns={"ds": "bucket"}).copy()
    rows["dim_name"], rows["dim_value"], rows["metric"] = dim_name, str(dim_value), metric
    rows["model_run_id"] = str(model_run_id)
    rows["fitted_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
    return rows[["bucket", "dim_name", "dim_value", "metric", "y", "yhat", "yhat_lower", "yhat_upper", "residual", "z", "is_anomaly", "model_run_id", "fitted_at"]]
