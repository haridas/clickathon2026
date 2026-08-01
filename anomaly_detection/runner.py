"""Command-line scheduler for finalized-day anomaly detection."""

import argparse
import logging
import time

from config import SUPPORTED_METRICS

from .scheduler import run_once


def main() -> None:
    parser = argparse.ArgumentParser(description="Run finalized-day RCA baseline jobs on a configured interval.")
    parser.add_argument("--check-every-seconds", type=int, default=3600, help="Scheduler polling interval; 3600 is a sensible default.")
    parser.add_argument("--days", type=int, default=35)
    parser.add_argument("--metric", action="append", choices=SUPPORTED_METRICS, default=None, help="Metric to process; repeat for a subset (all by default).")
    parser.add_argument("--once", action="store_true", help="Run one finalized-day check and exit.")
    args = parser.parse_args()
    if args.check_every_seconds < 60 or args.days < 21:
        raise ValueError("--check-every-seconds must be >= 60 and --days must be >= 21.")
    metrics = tuple(args.metric or SUPPORTED_METRICS)
    while True:
        try:
            run_once(args.days, metrics)
        except Exception:
            logging.exception("Scheduled detection run failed")
        if args.once:
            return
        time.sleep(args.check_every_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    main()
