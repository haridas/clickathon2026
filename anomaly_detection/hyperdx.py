"""Emit confirmed global revenue incidents as OpenTelemetry logs."""

from __future__ import annotations

import json
import logging
import os

import pandas as pd
from opentelemetry import _logs
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource


class HyperDXPublisher:
    """One process-scoped OTLP logger for confirmed anomaly notifications."""

    def __init__(self) -> None:
        service_name = os.getenv("OTEL_SERVICE_NAME", "ad-anomaly-detector")
        # The standard OTLP exporter reads authentication from this header. The
        # explicit environment value wins, so deployments using a different
        # collector authentication scheme remain supported.
        if os.getenv("HYPERDX_API_KEY") and not os.getenv("OTEL_EXPORTER_OTLP_HEADERS"):
            os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = f"authorization={os.environ['HYPERDX_API_KEY']}"
        provider = LoggerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
        _logs.set_logger_provider(provider)
        self.provider = provider
        self.logger = logging.getLogger("anomaly_detection.hyperdx")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self.logger.addHandler(LoggingHandler(level=logging.INFO, logger_provider=provider))

    def publish(self, global_rows: pd.DataFrame, contributors: pd.DataFrame) -> None:
        """Publish one global alert event per confirmed incident hour."""
        if global_rows.empty:
            return
        for row in global_rows.loc[global_rows.is_anomaly.eq(1)].itertuples(index=False):
            children = contributors.loc[contributors.bucket.eq(row.bucket)]
            payload = [{
                "dimension": item.dim_name,
                "segment": item.dim_value,
                "contribution_share": round(float(item.contribution_share), 4),
                "rank": int(item.contributor_rank),
            } for item in children.itertuples(index=False)]
            self.logger.info("anomaly_detected", extra={
                "event": "anomaly_detected",
                "metric": "revenue",
                "dim_name": "global",
                "dim_value": "all",
                "bucket": row.bucket.isoformat(),
                "actual": float(row.y),
                "expected": float(row.yhat),
                "residual": float(row.residual),
                "z_score": float(row.z),
                "severity": "high" if abs(float(row.z)) >= 3 else "medium",
                "direction": "drop" if row.residual < 0 else "spike",
                "contributors": json.dumps(payload),
            })
        self.provider.force_flush()
