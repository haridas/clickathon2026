"""Long-running Docker service for revenue detection and HyperDX delivery."""

from __future__ import annotations

import argparse
import logging
import time

from config import SUPPORTED_METRICS

from .hyperdx import HyperDXPublisher
from .scheduler import run_once


def main() -> None:
    parser = argparse.ArgumentParser(description="Run revenue anomaly detection and publish confirmed events to HyperDX.")
    parser.add_argument("--check-every-seconds", type=int, default=3600)
    parser.add_argument("--days", type=int, default=35)
    parser.add_argument("--metric", action="append", choices=SUPPORTED_METRICS, default=None)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.check_every_seconds < 60 or args.days < 21:
        raise ValueError("--check-every-seconds must be >= 60 and --days must be >= 21.")

    publisher = HyperDXPublisher()
    metrics = tuple(args.metric or ("revenue",))
    while True:
        try:
            result = run_once(args.days, metrics)
            if result is not None:
                _, baselines, contributors = result
                if "revenue" in metrics:
                    publisher.publish(baselines.get("revenue"), contributors)
        except Exception:
            logging.exception("Anomaly detection service run failed")
        if args.once:
            return
        time.sleep(args.check_every_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    main()
