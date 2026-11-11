"""Scoring metrics and odds helpers for the match predictor. Standard library only.

Outcomes are ordered; for 1X2 the order is (home, draw, away).
"""

from ._validation import PROB_SUM_TOLERANCE, MetricsError
from .calibration import CalibrationBucket, bucket_index, calibration_buckets
from .odds import MarginFree, consensus, remove_margin
from .scores import (
    AWAY,
    DRAW,
    HOME,
    LOG_LOSS_EPSILON,
    Prediction,
    brier,
    log_loss,
    mean_brier,
    mean_log_loss,
    mean_rps,
    rps,
)

__all__ = [
    "AWAY",
    "DRAW",
    "HOME",
    "LOG_LOSS_EPSILON",
    "PROB_SUM_TOLERANCE",
    "CalibrationBucket",
    "MarginFree",
    "MetricsError",
    "Prediction",
    "brier",
    "bucket_index",
    "calibration_buckets",
    "consensus",
    "log_loss",
    "mean_brier",
    "mean_log_loss",
    "mean_rps",
    "remove_margin",
    "rps",
]
