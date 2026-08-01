"""Continuously generate realistic, controllable ad events into ClickHouse."""

from __future__ import annotations

import argparse
from datetime import timedelta
import time

import numpy as np
import pandas as pd

from clickhouse_client import ClickHouseClient


class EventGenerator:
    def __init__(self, client: ClickHouseClient, seed: int, start: pd.Timestamp | None = None) -> None:
        self.client, self.rng = client, np.random.default_rng(seed)
        self.apps = client.client.query_df("SELECT app_id FROM apps")["app_id"].to_numpy()
        self.devices = client.client.query_df("SELECT geo_device_id FROM geo_device")["geo_device_id"].to_numpy()
        self.advertisers = client.client.query_df("SELECT advertiser_id FROM advertisers")["advertiser_id"].to_numpy()
        latest = client.client.query_df("SELECT max(event_time) AS latest FROM ad_events").iloc[0]["latest"]
        self.clock = start or (pd.Timestamp(latest).tz_localize(None) + timedelta(milliseconds=5))

    def batch(self, count: int, simulated_seconds: int, scenario: str) -> pd.DataFrame:
        times = self.clock + pd.to_timedelta(self.rng.uniform(0, simulated_seconds, count), unit="s")
        hour_factor = 0.85 + 0.25 * np.sin(2 * np.pi * self.clock.hour / 24)
        fill_probability, ctr, revenue_factor = 0.86 * hour_factor, 0.012, 1.0
        if scenario == "fill_rate_drop":
            fill_probability *= 0.5
        elif scenario == "ctr_drop":
            ctr *= 0.35
        elif scenario == "revenue_drop":
            revenue_factor = 0.35
        filled = self.rng.random(count) < fill_probability
        impressions = filled & (self.rng.random(count) < 0.93)
        clicks = impressions & (self.rng.random(count) < ctr)
        revenue = impressions * self.rng.lognormal(mean=-5.8, sigma=0.45, size=count) * revenue_factor
        self.clock += timedelta(seconds=simulated_seconds)
        return pd.DataFrame({
            "event_time": times, "app_id": self.rng.choice(self.apps, count),
            "geo_device_id": self.rng.choice(self.devices, count), "advertiser_id": self.rng.choice(self.advertisers, count),
            "ad_format": self.rng.choice(["banner", "native", "interstitial", "rewarded", "video"], count),
            "is_filled": filled.astype("uint8"), "is_impression": impressions.astype("uint8"),
            "is_click": clicks.astype("uint8"), "revenue": revenue,
        })


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate streaming ad_events for the RCA demo.")
    parser.add_argument("--interval-seconds", type=float, default=10, help="Wall-clock pause between batches.")
    parser.add_argument("--events-per-batch", type=int, default=5_000)
    parser.add_argument("--simulated-seconds-per-batch", type=int, default=60, help="How far event time advances each batch.")
    parser.add_argument("--scenario", choices=("normal", "revenue_drop", "fill_rate_drop", "ctr_drop"), default="normal")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--once", action="store_true", help="Insert one batch and exit.")
    args = parser.parse_args()
    if args.events_per_batch < 1 or args.interval_seconds < 0 or args.simulated_seconds_per_batch < 1:
        raise ValueError("Batch size and simulated time must be positive; interval cannot be negative.")
    generator = EventGenerator(ClickHouseClient(), args.seed)
    while True:
        events = generator.batch(args.events_per_batch, args.simulated_seconds_per_batch, args.scenario)
        generator.client.client.insert_df("ad_events", events)
        print(f"Inserted {len(events):,} {args.scenario} events through {generator.clock.isoformat()}", flush=True)
        if args.once:
            return
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
