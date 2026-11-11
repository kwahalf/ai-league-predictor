"""Dixon–Coles fundamentals model (plan step 2.4). Uses no odds.

- ``score_matrix``: the score matrix for given (λ, μ, ρ) and the markets derived from it
  (``ScoreForecast``: 1X2, xG, over/under, BTTS, most likely scorelines).
- ``fit``: the penalised, time-weighted maximum-likelihood fit (array level).
- ``model.DixonColesModel``: the backtest ``Model`` plus ``forecast(home, away)``.
"""

from .fit import RHO_BOUNDS, FitData, FittedParameters, TeamPrior, fit_parameters
from .model import (
    DEFAULT_HALF_LIFE_DAYS,
    DEFAULT_N_SEASONS,
    DEFAULT_PROMOTED_ATTACK,
    DEFAULT_PROMOTED_DEFENCE,
    DEFAULT_PROMOTED_PRECISION,
    DEFAULT_RIDGE_PRECISION,
    DixonColesModel,
    DixonColesParameters,
)
from .score_matrix import (
    DEFAULT_MAX_GOALS,
    ScoreForecast,
    Scoreline,
    poisson_pmf,
    score_matrix,
    tau_factors,
)

__all__ = [
    "DEFAULT_HALF_LIFE_DAYS",
    "DEFAULT_MAX_GOALS",
    "DEFAULT_N_SEASONS",
    "DEFAULT_PROMOTED_ATTACK",
    "DEFAULT_PROMOTED_DEFENCE",
    "DEFAULT_PROMOTED_PRECISION",
    "DEFAULT_RIDGE_PRECISION",
    "RHO_BOUNDS",
    "DixonColesModel",
    "DixonColesParameters",
    "FitData",
    "FittedParameters",
    "ScoreForecast",
    "Scoreline",
    "TeamPrior",
    "fit_parameters",
    "poisson_pmf",
    "score_matrix",
    "tau_factors",
]
