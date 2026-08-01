"""Rolling, leakage-free train/test evaluation for hourly Prophet forecasts."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import logging

import pandas as pd

from config import SUPPORTED_DIMENSIONS, SUPPORTED_METRICS

from .pipeline import AnomalyPipeline, DetectionRequest


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest hourly Prophet with rolling prior-history training windows.")
    parser.add_argument("--metric", choices=SUPPORTED_METRICS, required=True)
    parser.add_argument("--dimension", action="append", choices=SUPPORTED_DIMENSIONS, default=None)
    parser.add_argument("--train-days", type=int, default=21, help="Prior days used for every forecast; minimum 21.")
    parser.add_argument("--test-start", type=parse_date, required=True, help="First completed UTC test day.")
    parser.add_argument("--test-end", type=parse_date, required=True, help="Exclusive UTC test-day bound.")
    args = parser.parse_args()
    if args.train_days < 21 or args.test_start >= args.test_end:
        raise ValueError("--train-days must be at least 21 and --test-end must be after --test-start.")

    pipeline = AnomalyPipeline()
    dimensions = tuple(dict.fromkeys(args.dimension or SUPPORTED_DIMENSIONS))
    output = []
    target = args.test_start
    while target < args.test_end:
        output.append(pipeline.run(DetectionRequest(args.metric, dimensions, target, args.train_days, False)))
        target += timedelta(days=1)
    scored = pd.concat([frame for frame in output if not frame.empty], ignore_index=True) if output else pd.DataFrame()
    if scored.empty:
        print("No eligible test series. Ensure hourly rollups and sufficient prior history exist.")
        return
    error = scored.y - scored.yhat
    summary = pd.DataFrame([{
        "metric": args.metric,
        "test_hours": len(scored),
        "series": scored[["dim_name", "dim_value"]].drop_duplicates().shape[0],
        "mae": error.abs().mean(),
        "rmse": (error.pow(2).mean()) ** 0.5,
        "interval_coverage": ((scored.y >= scored.yhat_lower) & (scored.y <= scored.yhat_upper)).mean(),
        "anomaly_hours": int(scored.is_anomaly.sum()),
    }])
    print(summary.to_string(index=False))
    flagged = scored.loc[scored.is_anomaly.eq(1)]
    if not flagged.empty:
        print("\nConfirmed anomaly hours:")
        print(flagged.to_string(index=False))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    main()
