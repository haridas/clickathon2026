"""Daily Prophet anomaly-detection package for the InMobi RCA platform."""

from .pipeline import AnomalyPipeline, DetectionRequest

__all__ = ("AnomalyPipeline", "DetectionRequest")
