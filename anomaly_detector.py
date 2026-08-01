"""Backward-compatible import; use anomaly_detection.detector instead."""
from anomaly_detection.detector import ProphetAnomalyDetector

__all__ = ("ProphetAnomalyDetector",)
