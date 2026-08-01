"""Validation of daily Prophet input."""

import pandas as pd


def prepare_daily_series(frame: pd.DataFrame) -> pd.DataFrame:
    """Return unique, ordered observations; missing days are not invented."""
    return frame[["ds", "y"]].copy().dropna().sort_values("ds").drop_duplicates("ds", keep="last")
