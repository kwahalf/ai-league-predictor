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

1. *In the fit:* a team that plays in the latest season of the history but not in the season before
   it (it was promoted this season) gets a Gaussian prior pulling its attack toward the prior
   attack and its defence toward ``mean(defence) +`` the prior defence, with precision
   ``promoted_precision``. In the likelihood each match adds roughly λ ≈ 1.4 units of precision
   to a team's attack (and μ to its defence), so the default precision of 12 is worth about 8–9
   matches: after ~9 games a promoted team's own results count as much as the prior, and by the
   end of the season they dominate. Every other team gets a very weak pull (``ridge_precision``)
   toward the league average, which only matters for freak records in very few matches.
2. *Before it has played:* until a team has a result in the season being predicted, it is given
   the prior itself (the prior attack, and defence ``mean(defence) +`` the prior defence)
   unless it was in the league the season before: a team with no fitted strengths, or a side
   relegated earlier coming back, whose fitted strengths are stale. Established teams keep their
   fitted strengths, including one whose first fixture of a new season is delayed. See
   ``strengths``.

**The prior's means are estimated walk-forward** (step 2.6), at every ``fit``, from the history
that fit is given, so they never use a result on or after ``as_of``:

- A season ``S`` is *usable* when it is complete (a later season has started in the history, or
  ``as_of`` is on or after 1 August of the season's second year, ``SEASON_END_MONTH_DAY``; every
  Premier League season since 2016/17 ended by then, the latest on 26 July 2020) and the season
  before it is in the history too (so its promoted teams are known: the teams of ``S`` that
  didn't play ``S − 1``). Only matches before ``as_of`` are ever used, so the date rule can at
  worst count a season that hasn't finished, never a future result.
- For each usable season, the season's matches alone are fitted without decay and without the
  promoted-team prior (only the weak ridge pull); each promoted team contributes its attack and
  its defence minus that fit's mean defence. Those per-season fits are cached, since a complete
  season's matches don't change.
- The prior means are the plain average over all those promoted teams. Until at least
  ``min_promoted_teams`` (default 3, one season's worth) exist, the documented data-free
  fallback is used: ``DEFAULT_PROMOTED_FALLBACK_ATTACK`` and ``DEFAULT_PROMOTED_FALLBACK_DEFENCE``,
  both 0, i.e. "a promoted team is an average team" (no information, so no claimed handicap).
  With the E0 files from 2016/17 that means the fallback for 2017/18 only (2016/17's promoted
  teams can't be identified: 2015/16 isn't loaded), 3 teams from the first window of 2018/19 on,
  and 27 by 2025/26.

Before step 2.6 the means were fixed at −0.24 / −0.15, averaged from the promoted teams of
2017/18–2025/26, i.e. the same seasons the backtest scores (a leak). Passing ``promoted_attack``
and ``promoted_defence`` still fixes them (for tests and experiments); by default both are
``None`` and the walk-forward estimate is used.
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
DEFAULT_PROMOTED_FALLBACK_ATTACK = 0.0
DEFAULT_PROMOTED_FALLBACK_DEFENCE = 0.0
DEFAULT_MIN_PROMOTED_TEAMS = 3
SEASON_END_MONTH_DAY = (8, 1)
"""A season ``"SSEE"`` counts as complete from 1 August of year ``20EE`` (see the docstring)."""


def _season_complete_by(season: str) -> date | None:
    """1 August of the season's second year for a code like ``"1718"``; ``None`` otherwise."""
    if len(season) != 4 or not season.isdigit():
        return None
    return date(2000 + int(season[2:]), *SEASON_END_MONTH_DAY)
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
    previous_teams: frozenset[str]
    """Teams that played in the season before that (empty if the history has only one season)."""
    n_matches: int
    converged: bool
    prior_attack: float
    """Mean of the promoted-team prior on attack used in this fit (see the module docstring)."""
    prior_defence: float
    """Mean of the promoted-team prior on defence, relative to the league mean defence."""
    prior_n_teams: int
    """Promoted teams the prior was estimated from; 0 when it is fixed or the fallback."""
    prior_source: str
    """``"walk_forward"`` (estimated from the history), ``"fallback"`` or ``"fixed"``."""

    @property
    def mean_defence(self) -> float:
        return float(np.mean(list(self.defence.values())))


def _teams_in(matches: Sequence[MatchRecord], season: str) -> set[str]:
    return {t for r in matches if r.season == season for t in (r.home_team, r.away_team)}


_SeasonKey = tuple[str, tuple[tuple[date, str, str, int, int], ...]]


def _fit_data(
    matches: Sequence[MatchRecord], index: Mapping[str, int], weights: np.ndarray
) -> FitData:
    return FitData(
        home=np.array([index[r.home_team] for r in matches]),
        away=np.array([index[r.away_team] for r in matches]),
        home_goals=np.array([r.home_goals for r in matches], dtype=float),
        away_goals=np.array([r.away_goals for r in matches], dtype=float),
        weights=weights,
        n_teams=len(index),
    )


class DixonColesModel:
    """Time-weighted Dixon–Coles with a promoted-team prior. See the module docstring."""

    name = "dixon_coles"

    def __init__(
        self,
        *,
        half_life_days: float | None = DEFAULT_HALF_LIFE_DAYS,
        n_seasons: int = DEFAULT_N_SEASONS,
        max_goals: int = DEFAULT_MAX_GOALS,
        promoted_attack: float | None = None,
        promoted_defence: float | None = None,
        promoted_precision: float = DEFAULT_PROMOTED_PRECISION,
        ridge_precision: float = DEFAULT_RIDGE_PRECISION,
        min_promoted_teams: int = DEFAULT_MIN_PROMOTED_TEAMS,
        fallback_attack: float = DEFAULT_PROMOTED_FALLBACK_ATTACK,
        fallback_defence: float = DEFAULT_PROMOTED_FALLBACK_DEFENCE,
    ) -> None:
        if half_life_days is not None and not half_life_days > 0:
            raise ValueError("half_life_days must be positive (or None for no decay)")
        if n_seasons < 1:
            raise ValueError("n_seasons must be at least 1")
        if promoted_precision < 0 or ridge_precision < 0:
            raise ValueError("prior precisions must be non-negative")
        if (promoted_attack is None) != (promoted_defence is None):
            raise ValueError("fix both promoted_attack and promoted_defence, or neither")
        if min_promoted_teams < 1:
            raise ValueError("min_promoted_teams must be at least 1")
        self.half_life_days = half_life_days
        self.n_seasons = n_seasons
        self.max_goals = max_goals
        self.promoted_attack = promoted_attack
        self.promoted_defence = promoted_defence
        self.promoted_precision = promoted_precision
        self.ridge_precision = ridge_precision
        self.min_promoted_teams = min_promoted_teams
        self.fallback_attack = fallback_attack
        self.fallback_defence = fallback_defence
        # Promoted teams' (attack, defence − mean defence) per complete season, keyed by the
        # season's matches, so a cached entry is only reused for identical data.
        self._season_cache: dict[_SeasonKey, list[tuple[float, float]]] = {}
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
        # Promotion is judged on the season before the latest one in the whole history, so it
        # works whatever the window size (with n_seasons=1 that season is outside the window).
        previous = _teams_in(played, all_seasons[-2]) if len(all_seasons) >= 2 else set()
        promoted = current - previous if previous else set()

        ages = np.array([(as_of - r.match_date).days for r in matches], dtype=float)
        if self.half_life_days is None:
            weights = np.ones_like(ages)
        else:
            weights = 0.5 ** (ages / self.half_life_days)
        data = _fit_data(matches, index, weights)
        prior_attack, prior_defence, prior_n, prior_source = self._promoted_prior(
            played, all_seasons, as_of
        )
        is_promoted = np.array([t in promoted for t in teams])
        prior = TeamPrior(
            attack_mean=np.where(is_promoted, prior_attack, 0.0),
            defence_mean=np.where(is_promoted, prior_defence, 0.0),
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
            previous_teams=frozenset(previous),
            n_matches=len(matches),
            converged=fitted.converged,
            prior_attack=prior_attack,
            prior_defence=prior_defence,
            prior_n_teams=prior_n,
            prior_source=prior_source,
        )

    def _promoted_prior(
        self, played: Sequence[MatchRecord], seasons: Sequence[str], as_of: date
    ) -> tuple[float, float, int, str]:
        """(attack mean, defence mean, teams used, source) of the promoted-team prior, from
        ``played`` only (matches before ``as_of``). See the module docstring."""
        if self.promoted_attack is not None and self.promoted_defence is not None:
            return self.promoted_attack, self.promoted_defence, 0, "fixed"
        by_season: dict[str, list[MatchRecord]] = {}
        for r in played:
            by_season.setdefault(r.season, []).append(r)
        values: list[tuple[float, float]] = []
        # Usable: the season before is in the history, and this one is complete (a later season
        # has started, or its end date has passed). seasons is sorted, so seasons[i - 1] is the
        # one before seasons[i].
        for i in range(1, len(seasons)):
            end = _season_complete_by(seasons[i])
            if i == len(seasons) - 1 and (end is None or as_of < end):
                continue
            values.extend(
                self._season_promoted_strengths(
                    seasons[i], by_season[seasons[i]], _teams_in(by_season[seasons[i - 1]], seasons[i - 1])
                )
            )
        if len(values) < self.min_promoted_teams:
            return self.fallback_attack, self.fallback_defence, 0, "fallback"
        attack = float(np.mean([v[0] for v in values]))
        defence = float(np.mean([v[1] for v in values]))
        return attack, defence, len(values), "walk_forward"

    def _season_promoted_strengths(
        self, season: str, matches: Sequence[MatchRecord], previous_teams: set[str]
    ) -> list[tuple[float, float]]:
        """(attack, defence − mean defence) of each team promoted into ``season``, from a fit of
        that season's matches alone: no decay, no promoted-team prior, only the ridge pull."""
        key: _SeasonKey = (
            season,
            tuple(
                (r.match_date, r.home_team, r.away_team, int(r.home_goals), int(r.away_goals))  # type: ignore[arg-type]
                for r in matches
            ),
        )
        cached = self._season_cache.get(key)
        if cached is not None:
            return cached
        promoted = sorted(_teams_in(matches, season) - previous_teams)
        result: list[tuple[float, float]] = []
        if promoted:
            teams = sorted(_teams_in(matches, season))
            index = {t: i for i, t in enumerate(teams)}
            n = len(teams)
            fitted = fit_parameters(
                _fit_data(matches, index, np.ones(len(matches))),
                TeamPrior(np.zeros(n), np.zeros(n), np.full(n, self.ridge_precision)),
            )
            mean_def = float(fitted.defence.mean())
            result = [
                (float(fitted.attack[index[t]]), float(fitted.defence[index[t]]) - mean_def)
                for t in promoted
            ]
        self._season_cache[key] = result
        return result

    @property
    def parameters(self) -> DixonColesParameters:
        if self._params is None:
            raise RuntimeError("DixonColesModel.fit must be called before using the model")
        return self._params

    # --- prediction ------------------------------------------------------------------------

    def strengths(self, team: str, season: str | None = None) -> tuple[float, float]:
        """(attack, defence) used for ``team`` in a match of ``season`` (the promotion rules).

        ``season`` defaults to the latest fitted season. A team uses its fitted strengths if it has
        them and is in the league as far as the history shows: it has played in the latest fitted
        season, or (when predicting that same season) it played the season before, i.e. it's an
        established team that hasn't played yet this season. Anyone else (a new team, or one
        returning to the top flight) gets the promoted-team prior until its first result.
        """
        p = self.parameters
        in_league = team in p.current_teams or (
            (season is None or season == p.seasons[-1]) and team in p.previous_teams
        )
        if team in p.attack and in_league:
            return p.attack[team], p.defence[team]
        return p.prior_attack, p.mean_defence + p.prior_defence

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
