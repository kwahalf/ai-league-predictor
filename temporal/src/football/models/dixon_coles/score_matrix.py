"""The Dixon–Coles score matrix and every market derived from it.

``score_matrix(lam, mu, rho)`` is a pure function of the two expected-goals values and the
low-score correction, so later steps (the λ/μ solver of step 2.5) can build a matrix for any
(λ, μ) without a fitted model.

**The matrix.** ``P[i, j] = τ(i, j) · Poisson(i; λ) · Poisson(j; μ)`` for home goals ``i`` and
away goals ``j`` in ``0..max_goals`` (default 10), where the Dixon–Coles correction is

    τ(0, 0) = 1 − λμρ    τ(0, 1) = 1 + λρ    τ(1, 0) = 1 + μρ    τ(1, 1) = 1 − ρ

and ``τ = 1`` everywhere else. Over the infinite grid τ moves probability between the four
low-score cells without changing the total (or either side's expected goals): the four
corrections sum to ``ρ·e^(−λ−μ)·(−λμ + λμ + λμ − λμ) = 0``.

**Mass beyond ``max_goals``.** The grid is truncated at ``max_goals`` per side and then divided
by its own sum, so it sums to 1 and the (tiny) truncated mass is spread proportionally over the
kept cells. The truncated mass per side is about 6e-5 at a rate of 2.5 and 3e-4 at 3 (Premier
League rates are almost always below 3), so this barely moves any market. Expected goals are
reported as λ and μ themselves, not the slightly smaller means of the truncated matrix.

**Extreme ρ.** For large λ, μ or |ρ| a factor τ can go negative (e.g. ``1 − λμρ`` with λμρ > 1).
Such a factor is floored at 0 (that cell gets probability 0) before normalising, so a fitted model
always produces a valid matrix. This doesn't happen for realistic values: with |ρ| = 0.1 it needs
an expected-goals value above 10, or λμ above 10.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import lgamma

import numpy as np

DEFAULT_MAX_GOALS = 10


def poisson_pmf(rate: float, max_goals: int) -> np.ndarray:
    """``P(k; rate)`` for ``k = 0..max_goals``."""
    k = np.arange(max_goals + 1)
    log_fact = np.array([lgamma(i + 1.0) for i in k])
    return np.exp(k * np.log(rate) - rate - log_fact)


def tau_factors(lam: float, mu: float, rho: float) -> np.ndarray:
    """The 2×2 Dixon–Coles factors ``τ(i, j)`` for ``i, j ∈ {0, 1}``."""
    return np.array([[1.0 - lam * mu * rho, 1.0 + lam * rho], [1.0 + mu * rho, 1.0 - rho]])


def score_matrix(
    lam: float, mu: float, rho: float = 0.0, max_goals: int = DEFAULT_MAX_GOALS
) -> np.ndarray:
    """The normalised ``(max_goals + 1) × (max_goals + 1)`` matrix ``P[home_goals, away_goals]``."""
    if not (lam > 0 and mu > 0 and np.isfinite(lam) and np.isfinite(mu)):
        raise ValueError(f"expected goals must be positive and finite, got λ={lam}, μ={mu}")
    if max_goals < 1:
        raise ValueError("max_goals must be at least 1")
    tau = np.maximum(tau_factors(lam, mu, rho), 0.0)
    matrix = np.outer(poisson_pmf(lam, max_goals), poisson_pmf(mu, max_goals))
    matrix[:2, :2] *= tau
    return matrix / matrix.sum()


@dataclass(frozen=True)
class Scoreline:
    home_goals: int
    away_goals: int
    probability: float


@dataclass(frozen=True)
class ScoreForecast:
    """A score matrix with its expected goals, and every market derived from that one matrix."""

    lam: float
    """Home expected goals (λ)."""
    mu: float
    """Away expected goals (μ)."""
    rho: float
    matrix: np.ndarray
    """``matrix[i, j]`` = P(home scores ``i``, away scores ``j``); sums to 1."""

    @classmethod
    def from_rates(
        cls, lam: float, mu: float, rho: float = 0.0, max_goals: int = DEFAULT_MAX_GOALS
    ) -> "ScoreForecast":
        return cls(lam=lam, mu=mu, rho=rho, matrix=score_matrix(lam, mu, rho, max_goals))

    @property
    def expected_goals(self) -> tuple[float, float]:
        """(λ, μ): the model's expected goals for home and away."""
        return (self.lam, self.mu)

    def outcome_probabilities(self) -> tuple[float, float, float]:
        """(p_home, p_draw, p_away) from the matrix: below, on and above the diagonal."""
        home = float(np.tril(self.matrix, -1).sum())
        draw = float(np.trace(self.matrix))
        away = float(np.triu(self.matrix, 1).sum())
        total = home + draw + away  # 1 up to rounding; divide so the three sum to 1 exactly
        return (home / total, draw / total, away / total)

    def total_goals_distribution(self) -> np.ndarray:
        """``P(home + away = n)`` for ``n = 0..2·max_goals``."""
        size = self.matrix.shape[0]
        flipped = np.fliplr(self.matrix)
        return np.array([np.trace(flipped, offset=size - 1 - n) for n in range(2 * size - 1)])

    def over_under(self, line: float = 2.5) -> tuple[float, float]:
        """(P(total goals > line), P(total goals < line)) for a half-goal ``line``."""
        if line < 0.5 or line != line // 1 + 0.5:
            raise ValueError("only half-goal lines 0.5, 1.5, 2.5, ... are supported")
        totals = self.total_goals_distribution()
        under = float(totals[: int(line) + 1].sum())
        return (1.0 - under, under)

    def btts(self) -> tuple[float, float]:
        """(P(both teams score), P(at least one side scores nothing))."""
        yes = float(self.matrix[1:, 1:].sum())
        return (yes, 1.0 - yes)

    def top_scorelines(self, n: int = 5) -> tuple[Scoreline, ...]:
        """The ``n`` most likely scorelines, most likely first (ties: fewer home goals first)."""
        size = self.matrix.shape[0]
        cells = sorted(
            ((float(self.matrix[i, j]), i, j) for i in range(size) for j in range(size)),
            key=lambda c: (-c[0], c[1], c[2]),
        )
        return tuple(Scoreline(i, j, p) for p, i, j in cells[:n])
