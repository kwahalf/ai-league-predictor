"""Headline blend and score solver (plan step 2.5).

- ``logit``: the penalised multinomial logistic regression that blends the fundamentals 1X2, the
  market consensus 1X2 and the Elo difference (``BlendWeights``, ``fit_blend``).
- ``solver``: ``solve_scores``, the (λ, μ) whose Dixon–Coles matrix agrees with a headline 1X2
  and, when available, the consensus over/under 2.5.
- ``model.BlendModel``: the backtest ``Model`` plus ``forecast(view)`` → ``HeadlineForecast``.
"""

from .logit import (
    DEFAULT_FUNDAMENTALS_WEIGHT,
    DEFAULT_L2,
    DEFAULT_MARKET_WEIGHT,
    FEATURES,
    PROB_FLOOR,
    BlendWeights,
    features,
    fit_blend,
    prior_coefficients,
)
from .model import DEFAULT_MIN_TRAIN, BlendModel, HeadlineForecast, market_over_2_5
from .solver import DEFAULT_OU_WEIGHT, ONE_X_TWO_TOLERANCE, RATE_BOUNDS, SolvedScores, solve_scores

__all__ = [
    "DEFAULT_FUNDAMENTALS_WEIGHT",
    "DEFAULT_L2",
    "DEFAULT_MARKET_WEIGHT",
    "DEFAULT_MIN_TRAIN",
    "DEFAULT_OU_WEIGHT",
    "FEATURES",
    "ONE_X_TWO_TOLERANCE",
    "PROB_FLOOR",
    "RATE_BOUNDS",
    "BlendModel",
    "BlendWeights",
    "HeadlineForecast",
    "SolvedScores",
    "features",
    "fit_blend",
    "market_over_2_5",
    "prior_coefficients",
    "solve_scores",
]
