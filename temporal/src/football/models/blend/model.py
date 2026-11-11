"""``BlendModel``: the headline model (spec, "3. Headline model (with odds)").

It holds a ``DixonColesModel`` (fundamentals) and an ``EloModel`` and combines, per match, the
fundamentals 1X2, the margin-free pre-match consensus 1X2 and the Elo difference with the
penalised multinomial logit of ``logit`` (inputs, L2 penalty and its prior are documented there).
It implements the backtest ``Model`` protocol (``predict`` → headline 1X2) and, for step 2.7,
``forecast(view)`` → ``HeadlineForecast``: headline, fundamentals, Elo and market 1X2, whether odds
were used, and a score matrix that agrees with the headline (``solver.solve_scores``).

**No leakage, by construction.** The blend's weights are trained only on component predictions
made out of sample, before the result was known:

1. Each ``fit(history, as_of)`` refits both components on ``history`` (matches before ``as_of``).
2. Each ``predict``/``forecast`` of a match on or after ``as_of`` records that match's blend inputs
   (from components fitted without it), keyed by (season, home team, away team), the spec's unique
   match identity.
3. In a later ``fit``, once a recorded match appears in ``history`` with its result, its recorded
   inputs and that result become a training row. Components are never refitted on a match and then
   asked for a prediction to train on, so no training row is in-sample.

Recorded inputs are state carried between ``fit`` calls, so the model checks that each ``fit``
continues the previous one: ``as_of`` doesn't go backwards and ``history`` extends the previous
history (same matches, in the same order, with more appended). Otherwise it forgets everything
recorded and starts again, as on a fresh model.

**Minimum out-of-sample history.** The logit is fitted once at least ``min_train`` training rows
exist (default ``DEFAULT_MIN_TRAIN`` = 380, one Premier League season of predicted matches with
odds) and refitted every window after that on all rows so far (an expanding window). Before that
the headline is the penalty's prior, i.e. the margin-free market consensus with the default
prior (see ``logit``). In the backtest that means 2017/18, the first predicted season, is the
consensus, and the blend is fitted from the first window of 2018/19.

**Fallback.** A match with no usable pre-match 1X2 odds gets the fundamentals 1X2 as its headline,
with ``used_odds = False``, and its score forecast is the fundamentals model's own matrix. It adds
no training row (the logit needs the market input).

**Score matrix.** With odds, the headline 1X2 and the consensus P(over 2.5) (margin-free, equal
weight over the sources that price it; ``market_over_2_5``) go to ``solve_scores`` with the fitted
ρ, starting from the fundamentals' (λ, μ).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Sequence

import numpy as np

from ...backtest.inputs import MODEL_ODDS_SOURCES, MatchView
from ...backtest.market import market_consensus
from ...backtest.model import Probabilities
from ...data import MatchRecord, OddsQuote
from ...metrics import remove_margin
from ..dixon_coles import DixonColesModel, ScoreForecast
from ..elo import EloModel, elo_difference
from .logit import (
    DEFAULT_FUNDAMENTALS_WEIGHT,
    DEFAULT_L2,
    DEFAULT_MARKET_WEIGHT,
    BlendWeights,
    features,
    fit_blend,
    prior_coefficients,
)
from .solver import DEFAULT_OU_WEIGHT, SolvedScores, solve_scores

DEFAULT_MIN_TRAIN = 380
"""Out-of-sample training rows needed before the logit is fitted (one season)."""

_OUTCOME = {"H": 0, "D": 1, "A": 2}

MatchKey = tuple[str, str, str]
"""(season, home team, away team)."""


def market_over_2_5(quotes: Iterable[OddsQuote]) -> float | None:
    """Equal-weight consensus of the margin-free P(over 2.5) of ``MODEL_ODDS_SOURCES``.

    Uses the pre-match quotes whose over and under prices are both valid (finite, > 1.0);
    ``None`` if there are none."""
    by_source = {q.source: q for q in quotes if q.snapshot == "pre_match"}
    values: list[float] = []
    for source in MODEL_ODDS_SOURCES:
        quote = by_source.get(source)
        if quote is None:
            continue
        prices = (quote.over_2_5, quote.under_2_5)
        if any(p is None or not math.isfinite(p) or p <= 1.0 for p in prices):
            continue
        values.append(remove_margin(prices).probabilities[0])  # type: ignore[arg-type]
    return math.fsum(values) / len(values) if values else None


@dataclass(frozen=True)
class HeadlineForecast:
    """Everything the headline model says about one match (for step 2.7's ``predictions`` row)."""

    headline: Probabilities
    """The headline 1X2: the blend with odds, the fundamentals without."""
    fundamentals: Probabilities
    elo: Probabilities
    market: Probabilities | None
    """Margin-free pre-match consensus 1X2, or ``None`` without usable odds."""
    market_over_2_5: float | None
    """Margin-free consensus P(over 2.5), or ``None``."""
    used_odds: bool
    elo_diff: float
    """``(R_home + H − R_away) / 400``."""
    weights_trained: bool
    """Whether the headline used fitted blend weights: ``False`` when it used the prior (the
    consensus) or, without odds, no blend at all (the fundamentals)."""
    scores: ScoreForecast
    """Score matrix, xG, O/U, BTTS and top scorelines; agrees with ``headline`` within 0.5 points
    when ``scores_match_headline``."""
    scores_match_headline: bool
    """``True`` without odds (the fundamentals' own matrix) and, with odds, when the solver met
    its tolerance (``solved.within_tolerance``; see ``solver``, "Unattainable targets")."""
    solved: SolvedScores | None
    """The solver's result (fit quality) when odds were used; ``None`` otherwise."""


@dataclass(frozen=True)
class _Components:
    key: MatchKey
    fundamentals: Probabilities
    elo: Probabilities
    elo_diff: float
    market: Probabilities | None
    z: tuple[float, ...] | None


class BlendModel:
    """Headline blend of fundamentals, market consensus and Elo. See the module docstring."""

    name = "blend"

    def __init__(
        self,
        *,
        l2: float = DEFAULT_L2,
        min_train: int = DEFAULT_MIN_TRAIN,
        market_weight: float = DEFAULT_MARKET_WEIGHT,
        fundamentals_weight: float = DEFAULT_FUNDAMENTALS_WEIGHT,
        ou_weight: float = DEFAULT_OU_WEIGHT,
        fundamentals: DixonColesModel | None = None,
        elo: EloModel | None = None,
    ) -> None:
        if l2 <= 0:
            raise ValueError("l2 must be positive")
        if min_train < 1:
            raise ValueError("min_train must be at least 1")
        self.l2 = l2
        self.min_train = min_train
        self.ou_weight = ou_weight
        self.w0 = prior_coefficients(market_weight, fundamentals_weight)
        self.fundamentals = fundamentals or DixonColesModel()
        self.elo = elo or EloModel()
        self.as_of: date | None = None
        self._reset()

    def _reset(self) -> None:
        self._seen: list[MatchRecord] = []
        self._recorded: dict[MatchKey, tuple[float, ...]] = {}
        self._train_x: list[tuple[float, ...]] = []
        self._train_y: list[int] = []
        self._trained: BlendWeights | None = None
        self._trained_on = 0

    # --- fitting ---------------------------------------------------------------------------

    def _continues(self, history: Sequence[MatchRecord], as_of: date) -> bool:
        if self.as_of is not None and as_of < self.as_of:
            return False
        n = len(self._seen)
        return len(history) >= n and all(a is b or a == b for a, b in zip(history, self._seen))

    def fit(self, history: Sequence[MatchRecord], as_of: date) -> None:
        if not self._continues(history, as_of):
            self._reset()
        self.fundamentals.fit(history, as_of)
        self.elo.fit(history, as_of)
        for record in history[len(self._seen) :]:
            self._seen.append(record)
            z = self._recorded.pop((record.season, record.home_team, record.away_team), None)
            if z is not None and record.result in _OUTCOME and record.match_date < as_of:
                self._train_x.append(z)
                self._train_y.append(_OUTCOME[record.result])
        self.as_of = as_of
        n = len(self._train_y)
        if n >= self.min_train and n != self._trained_on:
            self._trained = fit_blend(
                np.array(self._train_x),
                np.array(self._train_y),
                l2=self.l2,
                w0=self.w0,
                start=self._trained,
            )
            self._trained_on = n

    @property
    def weights(self) -> BlendWeights:
        """The weights in use: fitted, or the prior until ``min_train`` rows exist."""
        return self._trained or BlendWeights.prior(self.w0, self.l2)

    @property
    def weights_trained(self) -> bool:
        return self._trained is not None

    @property
    def n_training_rows(self) -> int:
        """Out-of-sample training rows collected so far."""
        return len(self._train_y)

    # --- prediction ------------------------------------------------------------------------

    def _components(self, match: MatchView) -> _Components:
        if self.as_of is None:
            raise RuntimeError("BlendModel.fit must be called before predicting")
        f = self.fundamentals.predict(match)
        e = self.elo.predict(match)
        diff = elo_difference(
            self.elo.rating(match.home_team, match.season),
            self.elo.rating(match.away_team, match.season),
            self.elo.params.home_advantage,
        )
        m = market_consensus(match.odds)
        key = (match.season, match.home_team, match.away_team)
        z = features(f, m, diff) if m is not None else None
        # Record only matches the components can't have seen: on or after the fit's ``as_of``.
        if z is not None and match.match_date >= self.as_of:
            self._recorded[key] = z
        return _Components(key, f, e, diff, m, z)

    def _headline(self, c: _Components) -> Probabilities:
        if c.z is None:
            return c.fundamentals
        return self.weights.probabilities(c.z)

    def predict(self, match: MatchView) -> Probabilities:
        return self._headline(self._components(match))

    def forecast(self, match: MatchView) -> HeadlineForecast:
        """Headline, fundamentals, Elo and market 1X2, plus the score matrix agreeing with the
        headline (solved with odds; the fundamentals' own matrix without)."""
        c = self._components(match)
        headline = self._headline(c)
        dc_scores = self.fundamentals.forecast(match.home_team, match.away_team, match.season)
        over = market_over_2_5(match.odds)
        solved: SolvedScores | None = None
        if c.z is not None:
            solved = solve_scores(
                headline,
                self.fundamentals.parameters.rho,
                over_2_5=over,
                ou_weight=self.ou_weight,
                start=dc_scores.expected_goals,
                max_goals=self.fundamentals.max_goals,
            )
            scores = solved.forecast
        else:
            scores = dc_scores
        return HeadlineForecast(
            headline=headline,
            fundamentals=c.fundamentals,
            elo=c.elo,
            market=c.market,
            market_over_2_5=over,
            used_odds=c.z is not None,
            elo_diff=c.elo_diff,
            weights_trained=self.weights_trained and c.z is not None,
            scores=scores,
            scores_match_headline=solved is None or solved.within_tolerance,
            solved=solved,
        )
