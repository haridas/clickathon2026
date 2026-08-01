"""Model-specific settings for hourly, out-of-sample anomaly detection."""

import os

from dotenv import load_dotenv

load_dotenv()


PROPHET = {
    "yearly_seasonality": False,
    # Hour-of-day and day-of-week patterns are learned from the hourly rollup.
    "daily_seasonality": 10,
    "weekly_seasonality": 3,
    "seasonality_mode": "multiplicative",
    "changepoint_prior_scale": 0.01,
    "interval_width": float(os.getenv("PROPHET_INTERVAL_WIDTH", "0.99")),
}
MIN_HISTORY_HOURS = int(os.getenv("MIN_HISTORY_HOURS", str(21 * 24)))
FORECAST_HORIZON_HOURS = 24
MIN_HOURLY_REQUESTS = int(os.getenv("MIN_HOURLY_REQUESTS", "250"))
REVENUE_HISTORY_OUTLIER_RATIO = float(os.getenv("REVENUE_HISTORY_OUTLIER_RATIO", "0.15"))
REVENUE_TOP_CONTRIBUTORS = int(os.getenv("REVENUE_TOP_CONTRIBUTORS", "3"))

# A statistically unusual point must also have business materiality to alert.
# Rates are absolute changes; totals and eCPM are relative to the forecast.
MATERIALITY = {
    "revenue": ("relative", 0.10),
    "requests": ("relative", 0.10),
    "fill_rate": ("absolute", 0.02),
    "render_rate": ("absolute", 0.01),
    "ctr": ("absolute", 0.0015),
    "ecpm": ("relative", 0.10),
}
