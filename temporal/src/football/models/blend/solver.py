"""The λ/μ solver: a Dixon–Coles score matrix that agrees with a headline 1X2 (spec, "Scorelines
that agree with the headline 1X2").

The headline blend outputs 1X2 only. To get scorelines, expected goals, over/under and BTTS that
can't contradict it, ``solve_scores`` finds the expected-goals pair (λ, μ) whose Dixon–Coles matrix
(with the fitted ρ held fixed) best matches, by weighted least squares:

- the target 1X2 ``(p_home, p_draw, p_away)``: three residuals, weight 1 each;
- when given, the target P(over 2.5) (the margin-free consensus of the pre-match over/under
  prices): one residual, weight ``ou_weight`` (default ``DEFAULT_OU_WEIGHT`` = 0.1).

**Why the over/under weight is small.** With ρ fixed, (λ, μ) has two degrees of freedom and the
1X2 has two (the three probabilities sum to 1), so the 1X2 alone pins (λ, μ) down: the
supremacy λ − μ sets the home/away split and the total λ + μ sets the draw rate. Over/under is
also driven by λ + μ, so it can only be matched exactly when the market's draw and over/under
prices agree under the Dixon–Coles shape. When they disagree, the least-squares compromise moves
the 1X2 away from the headline. The headline 1X2 is the product, and the plan requires the matrix
to reproduce it within 0.5 percentage points (``ONE_X_TWO_TOLERANCE``), so the over/under gets a
small weight. Measured with ρ = −0.08: at weight 0.1 a 4-point disagreement moves the 1X2 by
0.03–0.15 points and an 8-point one by 0.05–0.29 points (larger for lopsided matches); at 0.25
a 4-point disagreement already costs about 0.4 points. The over/under therefore only nudges the
total goals: noticeably in lopsided matches, where the 1X2 barely constrains λ + μ (e.g. λ
moves from 4.0 to 4.4 for an 8-point gap), hardly at all in balanced ones. If a fit still misses
the 1X2 by more than the tolerance (very large disagreements), ``solve_scores`` re-solves on the
1X2 alone, so the over/under can never be what breaks the tolerance;
``SolvedScores.used_over_under`` records which.

**Unattainable targets.** A few 1X2 vectors can't be reached at all with expected goals inside
``RATE_BOUNDS`` (e.g. ``(0.499, 0.5, 0.001)``, which needs an away rate near 0). The solver then
returns the closest matrix it found and sets ``SolvedScores.within_tolerance`` to ``False``;
callers must check it before presenting the matrix as agreeing with the headline. Every real
consensus 1X2 of 2017/18–2025/26 is within tolerance (largest gap 0.41 points).

The search is over (log λ, log μ) within ``RATE_BOUNDS`` (0.02 to 8 goals) with
``scipy.optimize.least_squares``, started from a supplied guess (the fundamentals model's own
rates, normally) or from a closed-form Poisson approximation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares

from ..dixon_coles import DEFAULT_MAX_GOALS, ScoreForecast

DEFAULT_OU_WEIGHT = 0.1
"""Weight of the over/under 2.5 residual relative to each 1X2 residual. See the module docstring."""

ONE_X_TWO_TOLERANCE = 0.005
"""Largest acceptable gap between the solved matrix's 1X2 and the target (0.5 points)."""

RATE_BOUNDS = (0.02, 8.0)
"""Expected goals the solver may use, per side."""


@dataclass(frozen=True)
class SolvedScores:
    """The solved score forecast and how well it matches its targets."""

    forecast: ScoreForecast
    """Matrix, xG (λ, μ) and every derived market."""
    target_1x2: tuple[float, float, float]
    target_over_2_5: float | None
    used_over_under: bool
    """Whether the final solution includes the over/under residual (see the module docstring)."""
    max_abs_error_1x2: float
    """Largest |matrix 1X2 − target| over home, draw and away."""
    over_2_5_error: float | None
    """Matrix P(over 2.5) − target, or ``None`` without a target."""
    within_tolerance: bool
    """Whether ``max_abs_error_1x2 <= ONE_X_TWO_TOLERANCE`` (see "Unattainable targets")."""


def _check_target(target: tuple[float, float, float]) -> tuple[float, float, float]:
    values = tuple(float(p) for p in target)
    if len(values) != 3 or any(not math.isfinite(p) or p <= 0.0 or p >= 1.0 for p in values):
        raise ValueError(f"target 1X2 must be three probabilities in (0, 1), got {target!r}")
    if abs(sum(values) - 1.0) > 1e-6:
        raise ValueError(f"target 1X2 must sum to 1, got {target!r}")
    return values  # type: ignore[return-value]


def _initial_guess(target: tuple[float, float, float]) -> tuple[float, float]:
    """Rough (λ, μ): total goals from the draw rate, supremacy from the home/away gap."""
    p_home, p_draw, p_away = target
    total = float(np.clip(2.7 * (0.27 / p_draw) ** 1.5, 0.5, 7.0))
    supremacy = 2.2 * (p_home - p_away)
    lam = max(0.1, (total + supremacy) / 2)
    mu = max(0.1, (total - supremacy) / 2)
    return lam, mu


def _solve(
    target: tuple[float, float, float],
    over_2_5: float | None,
    rho: float,
    ou_weight: float,
    start: tuple[float, float],
    max_goals: int,
) -> ScoreForecast:
    def forecast_at(u: np.ndarray) -> ScoreForecast:
        return ScoreForecast.from_rates(float(np.exp(u[0])), float(np.exp(u[1])), rho, max_goals)

    def residuals(u: np.ndarray) -> np.ndarray:
        fc = forecast_at(u)
        r = list(np.subtract(fc.outcome_probabilities(), target))
        if over_2_5 is not None:
            r.append(ou_weight * (fc.over_under(2.5)[0] - over_2_5))
        return np.array(r)

    lo, hi = math.log(RATE_BOUNDS[0]), math.log(RATE_BOUNDS[1])
    u0 = np.clip(np.log(np.array(start, dtype=float)), lo + 1e-9, hi - 1e-9)
    fit = least_squares(
        residuals, u0, bounds=([lo, lo], [hi, hi]), xtol=1e-12, ftol=1e-12, gtol=1e-12
    )
    return forecast_at(fit.x)


def solve_scores(
    target_1x2: tuple[float, float, float],
    rho: float,
    *,
    over_2_5: float | None = None,
    ou_weight: float = DEFAULT_OU_WEIGHT,
    start: tuple[float, float] | None = None,
    max_goals: int = DEFAULT_MAX_GOALS,
) -> SolvedScores:
    """Find (λ, μ) whose Dixon–Coles matrix (with ``rho``) matches ``target_1x2`` and, if given,
    ``over_2_5`` = P(over 2.5 goals). See the module docstring."""
    target = _check_target(target_1x2)
    if over_2_5 is not None and not (0.0 < over_2_5 < 1.0):
        raise ValueError(f"over_2_5 must be a probability in (0, 1), got {over_2_5!r}")
    if ou_weight < 0:
        raise ValueError("ou_weight must be non-negative")
    guess = start if start is not None else _initial_guess(target)
    use_ou = over_2_5 is not None and ou_weight > 0
    fc = _solve(target, over_2_5 if use_ou else None, rho, ou_weight, guess, max_goals)
    error = max(abs(a - b) for a, b in zip(fc.outcome_probabilities(), target))
    if use_ou and error > ONE_X_TWO_TOLERANCE:
        # The over/under disagreed with the headline too much: the 1X2 wins.
        fc = _solve(target, None, rho, ou_weight, fc.expected_goals, max_goals)
        error = max(abs(a - b) for a, b in zip(fc.outcome_probabilities(), target))
        use_ou = False
    return SolvedScores(
        forecast=fc,
        target_1x2=target,
        target_over_2_5=over_2_5,
        used_over_under=use_ou,
        max_abs_error_1x2=error,
        over_2_5_error=None if over_2_5 is None else fc.over_under(2.5)[0] - over_2_5,
        within_tolerance=error <= ONE_X_TWO_TOLERANCE,
    )
