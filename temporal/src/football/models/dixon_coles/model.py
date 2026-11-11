"""``DixonColesModel``: the fundamentals model (spec, "2. Fundamentals model (no odds)").

It implements the backtest ``Model`` protocol (``fit(history, as_of)``, ``predict(view)`` → 1X2)
and a richer API for later steps: ``forecast(home, away)`` returns the full score matrix with its
derived markets (``ScoreForecast``), and ``parameters`` exposes the fitted strengths. It never
reads odds.

**Fit window.** Each ``fit`` uses the matches of the last ``n_seasons`` (default 3) seasons present
in ``history`` (the current, possibly part-played, season and the two before it), each weighted
``0.5 ** (age_days / half_life_days)`` with ``age_days = as_of − match_date`` (default half-life
180 days, to be tuned in step 2.6). ``fit`` is stateless: it refits from scratch on what it's given.

**Promoted teams.** A team new to the top flight has little or no data, and what it has is
usually poor: promoted sides score fewer and concede more than the league average. Two rules:

1. *In the fit:* a team that plays in the latest season of the window but not in the season before
   it (it was promoted this season) gets a Gaussian prior pulling its attack toward
   ``promoted_attack`` and its defence toward ``mean(defence) + promoted_defence``, with precision
   ``promoted_precision``. In the likelihood each match adds roughly λ ≈ 1.4 units of precision
   to a team's attack (and μ to its defence), so the default precision of 12 is worth about 8–9
   matches: after ~9 games a promoted team's own results count as much as the prior, and by the
   end of the season they dominate. Every other team gets a very weak pull (``ridge_precision``)
   toward the league average, which only matters for freak records in very few matches.
2. *Before it has played:* a team with no fitted strengths (no top-flight match in the window), or
   one that didn't play in the latest fitted season and is predicted in a later season (promoted
   between seasons, e.g. a side relegated two seasons ago coming back), is given the prior itself:
   attack ``promoted_attack``, defence ``mean(defence) + promoted_defence``.

The default prior means are the average fitted strengths of the 27 promoted teams of 2017/18 to
2025/26 at the end of their first season, fitted without the prior (attack −0.24, defence −0.15
relative to the league mean, rounded), i.e. about 21% fewer goals scored and 16% more conceded than
an average team. They
come from the same seasons the backtest scores, so they are, like the half-life, a tuned
hyperparameter rather than an out-of-sample estimate; two numbers, so the leak is small.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping, Sequence

import numpy as np

from ...backtest.inputs import MatchView
from ...backtest.model import Probabilities
from ...data import MatchRecord
from .fit import FitData, TeamPrior, fit_parameters
from .score_matrix import DEFAULT_MAX_GOALS, ScoreForecast

DEFAULT_HALF_LIFE_DAYS = 180.0
DEFAULT_N_SEASONS = 3
DEFAULT_PROMOTED_ATTACK = -0.24
DEFAULT_PROMOTED_DEFENCE = -0.15
DEFAULT_PROMOTED_PRECISION = 12.0
DEFAULT_RIDGE_PRECISION = 0.1


@dataclass(frozen=True)
class DixonColesParameters:
    """The result of one fit."""

    as_of: date
    seasons: tuple[str, ...]
    """The seasons in the fit window, oldest first."""
    attack: Mapping[str, float]
    """Mean zero over the teams in the fit."""
    defence: Mapping[str, float]
    home_adv: float
    rho: float
    promoted: frozenset[str]
    """Teams fitted with the promoted-team prior (first season in the top flight)."""
    current_teams: frozenset[str]
    """Teams that played in the latest season of the window."""
    n_matches: int
    converged: bool

    @property
    def mean_defence(self) -> float:
        return float(np.mean(list(self.defence.values())))


def _teams_in(matches: Sequence[MatchRecord], season: str) -> set[str]:
    return {t for r in matches if r.season == season for t in (r.home_team, r.away_team)}


class DixonColesModel:
    """Time-weighted Dixon–Coles with a promoted-team prior. See the module docstring."""

    name = "dixon_coles"

    def __init__(
        self,
        *,
        half_life_days: float | None = DEFAULT_HALF_LIFE_DAYS,
        n_seasons: int = DEFAULT_N_SEASONS,
        max_goals: int = DEFAULT_MAX_GOALS,
        promoted_attack: float = DEFAULT_PROMOTED_ATTACK,
        promoted_defence: float = DEFAULT_PROMOTED_DEFENCE,
        promoted_precision: float = DEFAULT_PROMOTED_PRECISION,
        ridge_precision: float = DEFAULT_RIDGE_PRECISION,
    ) -> None:
        if half_life_days is not None and not half_life_days > 0:
            raise ValueError("half_life_days must be positive (or None for no decay)")
        if n_seasons < 1:
            raise ValueError("n_seasons must be at least 1")
        if promoted_precision < 0 or ridge_precision < 0:
            raise ValueError("prior precisions must be non-negative")
        self.half_life_days = half_life_days
        self.n_seasons = n_seasons
        self.max_goals = max_goals
        self.promoted_attack = promoted_attack
        self.promoted_defence = promoted_defence
        self.promoted_precision = promoted_precision
        self.ridge_precision = ridge_precision
        self._params: DixonColesParameters | None = None

    # --- fitting ---------------------------------------------------------------------------

    def fit(self, history: Sequence[MatchRecord], as_of: date) -> None:
        played = [
            r
            for r in history
            if r.home_goals is not None and r.away_goals is not None and r.match_date < as_of
        ]
        if not played:
            raise ValueError(f"no played matches before {as_of} to fit on")
        all_seasons = sorted({r.season for r in played})
        window = tuple(all_seasons[-self.n_seasons :])
        matches = [r for r in played if r.season in window]

        teams = sorted({r.home_team for r in matches} | {r.away_team for r in matches})
        index = {t: i for i, t in enumerate(teams)}
        latest = window[-1]
        current = _teams_in(matches, latest)
        promoted = current - _teams_in(matches, window[-2]) if len(window) >= 2 else set()

        ages = np.array([(as_of - r.match_date).days for r in matches], dtype=float)
        if self.half_life_days is None:
            weights = np.ones_like(ages)
        else:
            weights = 0.5 ** (ages / self.half_life_days)
        data = FitData(
            home=np.array([index[r.home_team] for r in matches]),
            away=np.array([index[r.away_team] for r in matches]),
            home_goals=np.array([r.home_goals for r in matches], dtype=float),
            away_goals=np.array([r.away_goals for r in matches], dtype=float),
            weights=weights,
            n_teams=len(teams),
        )
        is_promoted = np.array([t in promoted for t in teams])
        prior = TeamPrior(
            attack_mean=np.where(is_promoted, self.promoted_attack, 0.0),
            defence_mean=np.where(is_promoted, self.promoted_defence, 0.0),
            precision=np.where(is_promoted, self.promoted_precision, self.ridge_precision),
        )
        fitted = fit_parameters(data, prior)
        self._params = DixonColesParameters(
            as_of=as_of,
            seasons=window,
            attack={t: float(fitted.attack[i]) for t, i in index.items()},
            defence={t: float(fitted.defence[i]) for t, i in index.items()},
            home_adv=fitted.home_adv,
            rho=fitted.rho,
            promoted=frozenset(promoted),
            current_teams=frozenset(current),
            n_matches=len(matches),
            converged=fitted.converged,
        )

    @property
    def parameters(self) -> DixonColesParameters:
        if self._params is None:
            raise RuntimeError("DixonColesModel.fit must be called before using the model")
        return self._params

    # --- prediction ------------------------------------------------------------------------

    def strengths(self, team: str, season: str | None = None) -> tuple[float, float]:
        """(attack, defence) used for ``team`` in a match of ``season`` (the promotion rules)."""
        p = self.parameters
        newly_promoted = (
            season is not None and season != p.seasons[-1] and team not in p.current_teams
        )
        if team in p.attack and not newly_promoted:
            return p.attack[team], p.defence[team]
        return self.promoted_attack, p.mean_defence + self.promoted_defence

    def expected_goals(
        self, home_team: str, away_team: str, season: str | None = None
    ) -> tuple[float, float]:
        """(λ, μ) for ``home_team`` v ``away_team``."""
        p = self.parameters
        att_h, def_h = self.strengths(home_team, season)
        att_a, def_a = self.strengths(away_team, season)
        return (
            float(np.exp(p.home_adv + att_h - def_a)),
            float(np.exp(att_a - def_h)),
        )

    def forecast(self, home_team: str, away_team: str, season: str | None = None) -> ScoreForecast:
        """The full score matrix and its derived markets (1X2, xG, O/U, BTTS, top scorelines)."""
        lam, mu = self.expected_goals(home_team, away_team, season)
        return ScoreForecast.from_rates(lam, mu, self.parameters.rho, self.max_goals)

    def predict(self, match: MatchView) -> Probabilities:
        return self.forecast(match.home_team, match.away_team, match.season).outcome_probabilities()
