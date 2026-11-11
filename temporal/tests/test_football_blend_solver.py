"""Tests for the λ/μ score solver (plan step 2.5: "solver within 0.5 points").

- Reproduces a target 1X2 within 0.5 percentage points over a grid of realistic and extreme
  matches (heavy home and away favourites, near coin-flips, very high and very low scoring).
- Matches the over/under 2.5 exactly when it is consistent with the 1X2, and keeps the 1X2 within
  tolerance when it isn't.
- Its markets all come from one matrix (xG, O/U, BTTS, top scores).
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from src.football.models.blend import ONE_X_TWO_TOLERANCE, solve_scores
from src.football.models.dixon_coles import ScoreForecast

RHOS = (-0.15, -0.08, 0.0, 0.05)
LAMS = (0.3, 0.7, 1.1, 1.5, 2.0, 2.8, 3.8)


def max_error(fc: ScoreForecast, target) -> float:
    return max(abs(a - b) for a, b in zip(fc.outcome_probabilities(), target))


@pytest.mark.parametrize("rho", RHOS)
def test_reproduces_dixon_coles_1x2_over_a_grid(rho: float) -> None:
    for lam, mu in itertools.product(LAMS, LAMS):
        target = ScoreForecast.from_rates(lam, mu, rho).outcome_probabilities()
        solved = solve_scores(target, rho)
        assert solved.max_abs_error_1x2 <= 1e-6, (lam, mu)
        assert solved.forecast.lam == pytest.approx(lam, rel=1e-3)
        assert solved.forecast.mu == pytest.approx(mu, rel=1e-3)


@pytest.mark.parametrize(
    "target",
    [
        (0.46, 0.26, 0.28),  # typical
        (0.36, 0.30, 0.34),  # coin flip
        (0.80, 0.13, 0.07),  # big home favourite
        (0.90, 0.07, 0.03),  # extreme home favourite
        (0.95, 0.035, 0.015),  # more extreme still
        (0.05, 0.12, 0.83),  # big away favourite
        (0.02, 0.06, 0.92),  # extreme away favourite
        (0.30, 0.40, 0.30),  # very drawish
        (0.40, 0.18, 0.42),  # very open
    ],
)
@pytest.mark.parametrize("rho", (-0.1, 0.0))
def test_hits_arbitrary_headlines_within_half_a_point(target, rho: float) -> None:
    solved = solve_scores(target, rho)
    assert solved.max_abs_error_1x2 <= ONE_X_TWO_TOLERANCE
    assert solved.within_tolerance
    assert solved.max_abs_error_1x2 == pytest.approx(max_error(solved.forecast, target))
    assert solved.forecast.rho == rho


@pytest.mark.parametrize("target", [(0.499, 0.5, 0.001), (0.001, 0.5, 0.499), (0.6, 0.399, 0.001)])
def test_unattainable_targets_are_flagged(target) -> None:
    """Targets that need an expected-goals value below RATE_BOUNDS can't be met: the result says
    so rather than passing as headline-consistent."""
    solved = solve_scores(target, 0.0, over_2_5=0.3)
    assert solved.max_abs_error_1x2 > ONE_X_TWO_TOLERANCE
    assert solved.max_abs_error_1x2 == pytest.approx(max_error(solved.forecast, target))
    assert not solved.within_tolerance


def test_market_shaped_headlines_with_over_under() -> None:
    """Realistic market-like inputs: 1X2 and over/under from a DC matrix plus a few points of
    disagreement on the over/under, as real prices have."""
    rng = np.random.default_rng(7)
    for _ in range(200):
        lam, mu = rng.uniform(0.4, 3.2, size=2)
        rho = rng.uniform(-0.12, 0.0)
        fc = ScoreForecast.from_rates(lam, mu, rho)
        target = fc.outcome_probabilities()
        over = float(np.clip(fc.over_under()[0] + rng.normal(0, 0.04), 0.05, 0.95))
        solved = solve_scores(target, rho, over_2_5=over)
        assert solved.max_abs_error_1x2 <= ONE_X_TWO_TOLERANCE


def test_matches_a_consistent_over_under_exactly() -> None:
    fc = ScoreForecast.from_rates(1.7, 1.2, -0.07)
    over = fc.over_under()[0]
    solved = solve_scores(fc.outcome_probabilities(), -0.07, over_2_5=over)
    assert solved.used_over_under
    assert abs(solved.over_2_5_error) <= 1e-6  # type: ignore[arg-type]
    assert solved.max_abs_error_1x2 <= 1e-6


def test_over_under_pulls_total_goals_its_way() -> None:
    fc = ScoreForecast.from_rates(2.8, 0.5, -0.08)
    target = fc.outcome_probabilities()
    plain = solve_scores(target, -0.08)
    higher = solve_scores(target, -0.08, over_2_5=fc.over_under()[0] + 0.06)
    assert higher.used_over_under
    assert sum(higher.forecast.expected_goals) > sum(plain.forecast.expected_goals)
    assert higher.max_abs_error_1x2 <= ONE_X_TWO_TOLERANCE


def test_a_wildly_inconsistent_over_under_cannot_break_the_1x2() -> None:
    target = ScoreForecast.from_rates(2.8, 0.5, -0.08).outcome_probabilities()
    solved = solve_scores(target, -0.08, over_2_5=0.05, ou_weight=1.0)
    assert not solved.used_over_under
    assert solved.max_abs_error_1x2 <= 1e-6


def test_markets_come_from_the_solved_matrix() -> None:
    solved = solve_scores((0.55, 0.24, 0.21), -0.08, over_2_5=0.52)
    fc = solved.forecast
    m = fc.matrix
    assert m.sum() == pytest.approx(1.0)
    assert fc.btts()[0] == pytest.approx(m[1:, 1:].sum())
    under = sum(m[i, j] for i in range(m.shape[0]) for j in range(m.shape[1]) if i + j <= 2)
    assert fc.over_under()[1] == pytest.approx(under)
    top = fc.top_scorelines(3)
    assert top[0].probability == pytest.approx(m.max())
    assert fc.expected_goals == (fc.lam, fc.mu)


def test_start_point_does_not_change_the_answer() -> None:
    target = (0.62, 0.22, 0.16)
    a = solve_scores(target, -0.05)
    b = solve_scores(target, -0.05, start=(0.5, 3.0))
    assert a.forecast.lam == pytest.approx(b.forecast.lam, rel=1e-5)
    assert a.forecast.mu == pytest.approx(b.forecast.mu, rel=1e-5)


@pytest.mark.parametrize(
    "target",
    [(0.5, 0.5, 0.0), (0.5, 0.3, 0.3), (1.0, 0.0, 0.0), (0.5, 0.25), (0.4, float("nan"), 0.6)],
)
def test_rejects_invalid_targets(target) -> None:
    with pytest.raises(ValueError):
        solve_scores(target, 0.0)


def test_rejects_invalid_over_under_and_weight() -> None:
    with pytest.raises(ValueError):
        solve_scores((0.4, 0.3, 0.3), 0.0, over_2_5=1.0)
    with pytest.raises(ValueError):
        solve_scores((0.4, 0.3, 0.3), 0.0, ou_weight=-1.0)
