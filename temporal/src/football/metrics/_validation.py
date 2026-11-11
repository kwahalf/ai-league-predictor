"""Input checks shared by the metrics modules.

Every public function validates its inputs and raises ``MetricsError`` (a ``ValueError``) with a
message naming the bad value, so a corrupt row fails loudly instead of skewing an average.
"""

from __future__ import annotations

import math
from typing import Sequence

# A probability vector may sum to 1 within this tolerance. Loose enough for 1X2 probabilities
# rounded to 6 decimal places (each off by up to 5e-7), tight enough to catch a missing outcome.
PROB_SUM_TOLERANCE = 1e-5


class MetricsError(ValueError):
    """Raised when a metrics function receives invalid input."""


def check_probs(probs: Sequence[float], *, name: str = "probs") -> tuple[float, ...]:
    """Return ``probs`` as a tuple of floats after checking it is a probability vector.

    Requires at least 2 outcomes, every value finite and in [0, 1], and the sum within
    ``PROB_SUM_TOLERANCE`` of 1.
    """
    try:
        values = tuple(float(p) for p in probs)
    except (TypeError, ValueError) as exc:
        raise MetricsError(f"{name} must be a sequence of numbers, got {probs!r}") from exc
    if len(values) < 2:
        raise MetricsError(f"{name} needs at least 2 outcomes, got {len(values)}")
    for p in values:
        if not math.isfinite(p) or p < 0.0 or p > 1.0:
            raise MetricsError(f"{name} must be finite and in [0, 1], got {values!r}")
    total = math.fsum(values)
    if abs(total - 1.0) > PROB_SUM_TOLERANCE:
        raise MetricsError(
            f"{name} must sum to 1 (within {PROB_SUM_TOLERANCE}), got {total!r} for {values!r}"
        )
    return values


def check_outcome(outcome: int, n_outcomes: int) -> int:
    """Return ``outcome`` after checking it is an integer index in ``[0, n_outcomes)``."""
    # bool is an int subclass; True/False as an outcome is almost certainly a bug.
    if isinstance(outcome, bool) or not isinstance(outcome, int):
        raise MetricsError(f"outcome must be an int index, got {outcome!r}")
    if not 0 <= outcome < n_outcomes:
        raise MetricsError(f"outcome must be in [0, {n_outcomes - 1}], got {outcome}")
    return outcome
