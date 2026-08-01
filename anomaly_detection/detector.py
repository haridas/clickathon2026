"""Daily Prophet prediction-interval anomaly scoring."""

import pandas as pd
from prophet import Prophet

from config import MIN_HISTORY_DAYS, PROPHET


class ProphetAnomalyDetector:
    def __init__(self, config: dict | None = None) -> None:
        self.config = config or PROPHET

    def score(self, history: pd.DataFrame) -> pd.DataFrame:
        if len(history) < MIN_HISTORY_DAYS:
            raise ValueError(f"At least {MIN_HISTORY_DAYS} daily observations are required.")
        model = Prophet(**self.config)
        model.fit(history[["ds", "y"]])
        forecast = model.predict(history[["ds"]])[["ds", "yhat", "yhat_lower", "yhat_upper"]]
        scored = history.merge(forecast, on="ds", how="left")
        scored["residual"] = scored["y"] - scored["yhat"]
        scored["z"] = scored["residual"] / (scored["yhat_upper"] - scored["yhat"]).clip(lower=1e-9)
        scored["is_anomaly"] = ((scored.y < scored.yhat_lower) | (scored.y > scored.yhat_upper)).astype("uint8")
        return scored
