"""Backward-compatible import; use anomaly_detection.pipeline instead."""
from anomaly_detection.pipeline import AnomalyPipeline, DetectionRequest

__all__ = ("AnomalyPipeline", "DetectionRequest")
