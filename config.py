"""Configuration and metric definitions for the daily RCA detection pipeline."""

from dataclasses import dataclass
import os

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    expression: str


# Ratios are calculated from daily totals, never averaged from precomputed ratios.
METRICS = {
    "revenue": MetricDefinition("revenue", "sum(m.revenue)"),
    "requests": MetricDefinition("requests", "sum(m.requests)"),
    "fill_rate": MetricDefinition("fill_rate", "if(sum(m.requests) = 0, 0., sum(m.fills) / sum(m.requests))"),
    "render_rate": MetricDefinition("render_rate", "if(sum(m.fills) = 0, 0., sum(m.impressions) / sum(m.fills))"),
    "ctr": MetricDefinition("ctr", "if(sum(m.impressions) = 0, 0., sum(m.clicks) / sum(m.impressions))"),
    "ecpm": MetricDefinition("ecpm", "if(sum(m.impressions) = 0, 0., 1000. * sum(m.revenue) / sum(m.impressions))"),
}

ROLLUP_DIMENSIONS = {
    "global": "'all'", "region": "coalesce(g.region, 'unknown')", "country": "coalesce(g.country, 'unknown')",
    "device_model": "coalesce(g.device_model, 'unknown')", "os_version": "coalesce(g.os_version, 'unknown')",
    "ad_format": "coalesce(e.ad_format, 'unknown')", "category": "coalesce(a.category, 'unknown')",
    "publisher_tier": "coalesce(a.publisher_tier, 'unknown')", "vertical": "coalesce(ad.vertical, 'unknown')",
    "campaign_type": "coalesce(ad.campaign_type, 'unknown')",
}

CLICKHOUSE = {
    "host": os.getenv("CLICKHOUSE_HOST"), "port": int(os.getenv("CLICKHOUSE_PORT", "8443")),
    "database": os.getenv("CLICKHOUSE_DATABASE", "hackathon"), "username": os.getenv("CLICKHOUSE_USER"),
    "password": os.getenv("CLICKHOUSE_PASSWORD"), "secure": os.getenv("CLICKHOUSE_SECURE", "true").lower() == "true",
}
METRICS_HOURLY_TABLE = os.getenv("METRICS_HOURLY_TABLE", "metrics_hourly")
HOURLY_BASELINES_TABLE = os.getenv("HOURLY_BASELINES_TABLE", "metric_baselines_hourly")
SUPPORTED_METRICS = tuple(METRICS)
SUPPORTED_DIMENSIONS = tuple(ROLLUP_DIMENSIONS)
