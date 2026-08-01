"""Validation of hourly Prophet input."""

import pandas as pd


def prepare_hourly_series(frame: pd.DataFrame) -> pd.DataFrame:
    """Return unique, ordered observations; missing hours are not invented."""
    return frame[["ds", "y"]].copy().dropna().sort_values("ds").drop_duplicates("ds", keep="last")


def clean_revenue_history(history: pd.DataFrame, minimum_relative_gap: float) -> pd.DataFrame:
    """Replace persistent historical revenue shocks with their same-hour-week baseline.

    Prophet documentation warns that extreme historical outliers can corrupt later
    seasonal forecasts. A target is never modified; this operates only on history.
    """
    cleaned = history.copy().sort_values("ds")
    key = cleaned.ds.dt.dayofweek * 24 + cleaned.ds.dt.hour
    grouped = cleaned.groupby(key)["y"]
    median = grouped.transform("median")
    count = grouped.transform("count")
    relative_gap = (cleaned.y - median).abs() / median.abs().clip(lower=1e-9)
    candidate = (count >= 3) & (relative_gap >= minimum_relative_gap)
    # Do not erase a one-hour promotion or a delayed batch.  A training shock
    # must have the same persistence requirement as an alert before it is
    # treated as contamination of the normal baseline.
    candidate_run = candidate.ne(candidate.shift()).cumsum()
    persistent = candidate.groupby(candidate_run).transform("sum").ge(2)
    outlier = candidate & persistent
    cleaned.loc[outlier, "y"] = median.loc[outlier]
    return cleaned
