"""Calibration buckets: does "40%" happen about 40% of the time?

Every outcome probability of every prediction counts as one forecast. A 1X2 prediction
(0.5, 0.3, 0.2) with a home win adds three forecasts: 0.5 (happened), 0.3 (didn't), 0.2 (didn't).

Bucket edges, for the default 10 buckets: [0.0, 0.1), [0.1, 0.2), ..., [0.8, 0.9), [0.9, 1.0].
Each bucket includes its lower edge; the last one also includes 1.0.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from ._validation import MetricsError, check_outcome, check_probs
from .scores import Prediction


@dataclass(frozen=True)
class CalibrationBucket:
    lower: float
    upper: float
    count: int
    """Number of forecasts whose probability falls in [lower, upper)."""
    mean_predicted: float | None
    """Mean forecast probability in the bucket; None when the bucket is empty."""
    observed_frequency: float | None
    """Share of the bucket's forecasts whose outcome happened; None when the bucket is empty."""


def bucket_index(p: float, n_buckets: int = 10) -> int:
    """Index of the bucket that probability ``p`` falls in (1.0 goes in the last bucket).

    ``p * n_buckets`` can round across an edge (``0.29 * 100`` is ``28.999...``), so the candidate
    is corrected against the same ``b / n_buckets`` edges that ``calibration_buckets`` reports.
    """
    b = int(math.floor(p * n_buckets))
    if b + 1 < n_buckets and (b + 1) / n_buckets <= p:
        b += 1
    elif b > 0 and b / n_buckets > p:
        b -= 1
    return max(0, min(b, n_buckets - 1))


def calibration_buckets(
    predictions: Iterable[Prediction], n_buckets: int = 10
) -> list[CalibrationBucket]:
    """Group every outcome probability into ``n_buckets`` equal-width buckets.

    Returns all buckets in order, including empty ones (count 0, means None), so the result always
    has ``n_buckets`` entries.
    """
    if isinstance(n_buckets, bool) or not isinstance(n_buckets, int) or n_buckets < 1:
        raise MetricsError(f"n_buckets must be a positive int, got {n_buckets!r}")
    sums = [0.0] * n_buckets
    hits = [0] * n_buckets
    counts = [0] * n_buckets
    for probs, outcome in predictions:
        p = check_probs(probs)
        outcome = check_outcome(outcome, len(p))
        for i, pi in enumerate(p):
            b = bucket_index(pi, n_buckets)
            counts[b] += 1
            sums[b] += pi
            hits[b] += 1 if i == outcome else 0
    return [
        CalibrationBucket(
            lower=b / n_buckets,
            upper=(b + 1) / n_buckets,
            count=counts[b],
            mean_predicted=sums[b] / counts[b] if counts[b] else None,
            observed_frequency=hits[b] / counts[b] if counts[b] else None,
        )
        for b in range(n_buckets)
    ]
