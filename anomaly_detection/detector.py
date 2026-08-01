"""Out-of-sample hourly Prophet prediction-interval anomaly scoring."""

import pandas as pd
from prophet import Prophet

from .config import MIN_HISTORY_HOURS, PROPHET


class ProphetAnomalyDetector:
    def __init__(self, config: dict | None = None) -> None:
        self.config = config or PROPHET

    def score(self, history: pd.DataFrame, actual: pd.DataFrame) -> pd.DataFrame:
        """Forecast target timestamps from prior observations only."""
        if len(history) < MIN_HISTORY_HOURS:
            raise ValueError(f"At least {MIN_HISTORY_HOURS} hourly observations are required.")
        if actual.empty:
            raise ValueError("At least one completed target-hour observation is required.")
        model = Prophet(**self.config)
        model.fit(history[["ds", "y"]])
        forecast = model.predict(actual[["ds"]])[["ds", "yhat", "yhat_lower", "yhat_upper"]]
        scored = actual.merge(forecast, on="ds", how="left")
        scored["residual"] = scored["y"] - scored["yhat"]
        width = (scored["yhat_upper"] - scored["yhat"]).where(
            scored["residual"] >= 0, scored["yhat"] - scored["yhat_lower"]
        )
        scored["z"] = scored["residual"] / width.clip(lower=1e-9)
        scored["is_anomaly"] = ((scored.y < scored.yhat_lower) | (scored.y > scored.yhat_upper)).astype("uint8")
        return scored
