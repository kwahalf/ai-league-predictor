"""Proper scoring rules for ordered match outcomes. Lower is better for all three.

A forecast is a probability vector over ordered outcomes; for 1X2 the order is
(home, draw, away), i.e. ``HOME = 0``, ``DRAW = 1``, ``AWAY = 2``. The observed outcome is the
index of what happened.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

from ._validation import MetricsError, check_outcome, check_probs

HOME, DRAW, AWAY = 0, 1, 2

# Probabilities are clipped to [LOG_LOSS_EPSILON, 1] before taking the log, so a forecast that gave
# the observed outcome probability 0 scores -ln(1e-15) ≈ 34.54 instead of infinity.
LOG_LOSS_EPSILON = 1e-15

Prediction = tuple[Sequence[float], int]
"""One scored forecast: (probabilities, observed outcome index)."""


def rps(probs: Sequence[float], outcome: int) -> float:
    """Ranked probability score (Constantinou & Fenton 2012), in [0, 1].

    RPS = 1/(r-1) * sum_{i=1}^{r-1} (P_i - O_i)^2, where r is the number of outcomes and P_i, O_i
    are the cumulative forecast and observed probabilities up to outcome i. It rewards putting
    probability near the observed outcome: for a home win, a draw forecast is "less wrong" than an
    away forecast.
    """
    p = check_probs(probs)
    outcome = check_outcome(outcome, len(p))
    cum_p = 0.0
    total = 0.0
    for i in range(len(p) - 1):
        cum_p += p[i]
        cum_o = 1.0 if outcome <= i else 0.0
        total += (cum_p - cum_o) ** 2
    return total / (len(p) - 1)


def log_loss(probs: Sequence[float], outcome: int) -> float:
    """Negative natural log of the probability given to the observed outcome.

    The probability is clipped to at least ``LOG_LOSS_EPSILON`` (1e-15), so the result is in
    [0, ~34.54].
    """
    p = check_probs(probs)
    outcome = check_outcome(outcome, len(p))
    return -math.log(max(p[outcome], LOG_LOSS_EPSILON))


def brier(probs: Sequence[float], outcome: int) -> float:
    """Multi-class Brier score: sum over outcomes of (p_i - o_i)^2, in [0, 2].

    This is Brier's original definition (summed, not averaged over the outcomes), so a certain and
    wrong forecast scores 2 and a certain and right one scores 0.
    """
    p = check_probs(probs)
    outcome = check_outcome(outcome, len(p))
    return math.fsum((pi - (1.0 if i == outcome else 0.0)) ** 2 for i, pi in enumerate(p))


def _mean(score, predictions: Iterable[Prediction]) -> float:
    values = [score(probs, outcome) for probs, outcome in predictions]
    if not values:
        raise MetricsError("need at least one prediction to average")
    return math.fsum(values) / len(values)


def mean_rps(predictions: Iterable[Prediction]) -> float:
    """Mean RPS over ``(probs, outcome)`` pairs. Raises ``MetricsError`` if there are none."""
    return _mean(rps, predictions)


def mean_log_loss(predictions: Iterable[Prediction]) -> float:
    """Mean log loss over ``(probs, outcome)`` pairs. Raises ``MetricsError`` if there are none."""
    return _mean(log_loss, predictions)


def mean_brier(predictions: Iterable[Prediction]) -> float:
    """Mean Brier score over ``(probs, outcome)`` pairs. Raises ``MetricsError`` if there are none."""
    return _mean(brier, predictions)
