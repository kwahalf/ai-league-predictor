"""Elo baseline (plan step 2.3; spec, "Prediction model", 1). Standard library only.

Two parts:

1. **Elo ratings**, updated match by match in date order from the history passed to ``fit``. The
   home side's expected score is ``E = 1 / (1 + 10 ** (-(R_home + H - R_away) / 400))`` and the
   update is ``R_home += K * (S - E)``, ``R_away -= K * (S - E)`` with ``S`` = 1 / 0.5 / 0 for a
   home win / draw / away win. Odds are never used.
2. An **ordered logit** maps the pre-match rating difference to 1X2. With
   ``x = (R_home + H - R_away) / 400`` and outcomes ordered away < draw < home::

       P(away) = F(c1 - b*x)
       P(draw) = F(c2 - b*x) - F(c1 - b*x)
       P(home) = 1 - F(c2 - b*x)

   where ``F`` is the logistic function. The two cut-points ``c1 < c2`` and the slope ``b`` are
   fitted by maximum likelihood (Newton's method with step halving; the log-likelihood is concave)
   on every match in ``fit``'s history, each with the ratings *before* that match, so the mapping
   is learned from genuinely pre-match differences.

**Seasons.** When the history moves to a new season, every rating is regressed towards the mean
of the finished season's teams by ``season_regression`` (squad turnover makes old form less
informative), and teams that didn't play the previous season are dropped from the table.

**Promoted teams** have no Premier League history. A team that didn't play the previous season
starts at the mean rating, after regression, of the ``n_relegated`` teams that finished bottom of
the previous season's table (points, then goal difference, then goals scored), plus
``promoted_offset`` (default 0). Why: promoted teams are, on average, about as strong as the teams
they replace (both sit at the boundary between the Premier League and the Championship); the
relegated teams' ratings are on the same Elo scale, need no extra tuned constant, and follow any
drift in the league's overall level. A fixed offset below the mean would have to be tuned and goes
stale. In the first season of the history (no previous season) every team starts at
``initial_rating``.

**Leakage.** The model learns only from ``fit``'s history (played matches before ``as_of``) and
predicts from the ``MatchView`` (teams and season). It carries no state from earlier ``fit`` calls
except a cache of work over a history prefix that is checked to be identical.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Sequence

from ..backtest.inputs import MatchView
from ..backtest.model import Probabilities
from ..data import MatchRecord

ELO_SCALE = 400.0
"""Rating points per factor of 10 in the odds of the expected score, and the unit of ``x``."""


class OrderedLogitError(ValueError):
    """The ordered logit can't be fitted to the data given (too few or degenerate matches)."""


@dataclass(frozen=True)
class EloParams:
    """Elo parameters. Defaults are standard football-Elo values (spec: K about 20 with a home
    advantage), not tuned on the backtest. A one-at-a-time check over K 15-30, home advantage
    40-80, regression 0-1/3 and promoted offset -50..+50 moved the 2017/18-2025/26 RPS by at most
    0.0008 (0.2017 at the defaults); the relegated-teams rule (offset 0) was the best of the three
    offsets tried."""

    k: float = 20.0
    """Update size: rating points moved by a fully unexpected result."""
    home_advantage: float = 60.0
    """Rating points added to the home side (about a 58.5% expected score between equals)."""
    initial_rating: float = 1500.0
    """Rating of every team in the first season of the history."""
    season_regression: float = 0.2
    """Fraction of each rating's distance from the league mean removed between seasons."""
    promoted_offset: float = 0.0
    """Added to the relegated teams' mean rating to give a promoted team's starting rating."""
    n_relegated: int = 3
    """Teams relegated each season (the bottom of the table)."""

    def __post_init__(self) -> None:
        if not (self.k > 0 and math.isfinite(self.k)):
            raise ValueError(f"k must be positive and finite, got {self.k!r}")
        if not math.isfinite(self.home_advantage):
            raise ValueError(f"home_advantage must be finite, got {self.home_advantage!r}")
        if not math.isfinite(self.initial_rating):
            raise ValueError(f"initial_rating must be finite, got {self.initial_rating!r}")
        if not 0.0 <= self.season_regression <= 1.0:
            raise ValueError(
                f"season_regression must be in [0, 1], got {self.season_regression!r}"
            )
        if not math.isfinite(self.promoted_offset):
            raise ValueError(f"promoted_offset must be finite, got {self.promoted_offset!r}")
        if self.n_relegated < 1:
            raise ValueError(f"n_relegated must be at least 1, got {self.n_relegated!r}")


# --- Elo ----------------------------------------------------------------------------------------

_SCORE = {"H": 1.0, "D": 0.5, "A": 0.0}


def elo_difference(home_rating: float, away_rating: float, home_advantage: float) -> float:
    """``x = (R_home + H - R_away) / 400``: the ordered logit's input."""
    return (home_rating + home_advantage - away_rating) / ELO_SCALE


def expected_home_score(home_rating: float, away_rating: float, home_advantage: float) -> float:
    """The home side's expected score (win 1, draw 0.5), including home advantage."""
    return 1.0 / (1.0 + 10.0 ** (-elo_difference(home_rating, away_rating, home_advantage)))


def elo_update(
    home_rating: float, away_rating: float, result: str, k: float, home_advantage: float
) -> tuple[float, float]:
    """New (home, away) ratings after a match with ``result`` ``"H"``, ``"D"`` or ``"A"``."""
    try:
        score = _SCORE[result]
    except KeyError:
        raise ValueError(f"result must be 'H', 'D' or 'A', got {result!r}") from None
    delta = k * (score - expected_home_score(home_rating, away_rating, home_advantage))
    return home_rating + delta, away_rating - delta


def _table_order(table: dict[str, list[int]]) -> list[str]:
    """Teams worst first: points, goal difference, goals scored, then name (deterministic)."""
    return sorted(table, key=lambda t: (table[t][0], table[t][1], table[t][2], t))


class EloRatings:
    """Elo ratings fed one played match at a time, oldest first, with season transitions."""

    def __init__(self, params: EloParams | None = None) -> None:
        self.params = params or EloParams()
        self.season: str | None = None
        """Season of the last match processed."""
        self.ratings: dict[str, float] = {}
        """Current ratings: this season's teams, plus last season's teams yet to play."""
        self.promoted_rating: float = self.params.initial_rating
        """Starting rating of a team new to this season."""
        self._table: dict[str, list[int]] = {}
        """This season's table so far: team -> [points, goal difference, goals scored]."""

    def next_season_start(self) -> tuple[dict[str, float], float]:
        """Ratings and promoted-team rating for the season after ``self.season`` (no mutation)."""
        p = self.params
        if self.season is None or not self._table:
            return dict(self.ratings), p.initial_rating
        teams = list(self._table)
        mean = math.fsum(self.ratings[t] for t in teams) / len(teams)
        keep = 1.0 - p.season_regression
        regressed = {t: mean + keep * (self.ratings[t] - mean) for t in teams}
        relegated = _table_order(self._table)[: p.n_relegated]
        promoted = (
            math.fsum(regressed[t] for t in relegated) / len(relegated) + p.promoted_offset
        )
        return regressed, promoted

    def rating(self, team: str, season: str | None = None) -> float:
        """``team``'s rating for a match in ``season`` (default: the current season).

        A season other than the current one is treated as the next season: ratings are regressed
        and teams new to the league get the promoted-team rating."""
        if season is None or season == self.season:
            return self.ratings.get(team, self.promoted_rating)
        ratings, promoted = self.next_season_start()
        return ratings.get(team, promoted)

    def ratings_for(self, season: str | None = None) -> dict[str, float]:
        """A copy of every known team's rating for ``season`` (see ``rating``)."""
        if season is None or season == self.season:
            return dict(self.ratings)
        return self.next_season_start()[0]

    def process(self, record: MatchRecord) -> float:
        """Apply one played match. Returns its pre-match ``x`` (see ``elo_difference``)."""
        if record.result is None or record.home_goals is None or record.away_goals is None:
            raise ValueError(
                f"{record.match_date} {record.home_team} v {record.away_team} has no result"
            )
        if record.season != self.season:
            if self.season is not None:
                self.ratings, self.promoted_rating = self.next_season_start()
            self.season = record.season
            self._table = {}
        p = self.params
        home = self.ratings.setdefault(record.home_team, self.promoted_rating)
        away = self.ratings.setdefault(record.away_team, self.promoted_rating)
        x = elo_difference(home, away, p.home_advantage)
        self.ratings[record.home_team], self.ratings[record.away_team] = elo_update(
            home, away, record.result, p.k, p.home_advantage
        )
        hg, ag = record.home_goals, record.away_goals
        home_row = self._table.setdefault(record.home_team, [0, 0, 0])
        away_row = self._table.setdefault(record.away_team, [0, 0, 0])
        home_row[0] += {"H": 3, "D": 1, "A": 0}[record.result]
        away_row[0] += {"H": 0, "D": 1, "A": 3}[record.result]
        home_row[1] += hg - ag
        away_row[1] += ag - hg
        home_row[2] += hg
        away_row[2] += ag
        return x


# --- Ordered logit ------------------------------------------------------------------------------

# Outcome categories in the ordered logit's order (away < draw < home).
_CATEGORY = {"A": 0, "D": 1, "H": 2}


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


@dataclass(frozen=True)
class OrderedLogit:
    """Ordered-logit mapping from ``x`` to (home, draw, away); see the module docstring."""

    cut_away: float
    """``c1``: P(away) = F(c1 - b*x)."""
    cut_draw: float
    """``c2``: P(away or draw) = F(c2 - b*x). Must exceed ``cut_away``."""
    slope: float
    """``b``: how strongly the rating difference moves the outcome towards a home win."""

    def __post_init__(self) -> None:
        values = (self.cut_away, self.cut_draw, self.slope)
        if not all(math.isfinite(v) for v in values):
            raise ValueError(f"ordered-logit parameters must be finite, got {values!r}")
        if not self.cut_away < self.cut_draw:
            raise ValueError(f"cut_away must be below cut_draw, got {values!r}")

    def probabilities(self, x: float) -> Probabilities:
        """(p_home, p_draw, p_away) for rating difference ``x``."""
        f_away = _sigmoid(self.cut_away - self.slope * x)
        f_away_or_draw = _sigmoid(self.cut_draw - self.slope * x)
        p_away = f_away
        p_draw = max(f_away_or_draw - f_away, 0.0)
        p_home = max(1.0 - f_away_or_draw, 0.0)
        total = p_home + p_draw + p_away
        return (p_home / total, p_draw / total, p_away / total)


DEFAULT_LOGIT = OrderedLogit(cut_away=-0.45, cut_draw=0.67, slope=2.75)
"""Used when the history is too small or degenerate to fit (fewer than ``min_fit_matches``
matches, an outcome never seen, or no convergence). Rounded from a fit on all ten Premier League
seasons 2016/17-2025/26 with the default ``EloParams``: about 44% home, 27% draw, 30% away between
equal teams. The backtest never uses it: its first window already has a full season of history."""

_TINY = 1e-300


def _log_likelihood(theta: Sequence[float], xs: Sequence[float], ks: Sequence[int]) -> float:
    c1, c2, b = theta
    total = 0.0
    for x, k in zip(xs, ks):
        upper = 1.0 if k == 2 else _sigmoid((c1 if k == 0 else c2) - b * x)
        lower = 0.0 if k == 0 else _sigmoid((c1 if k == 1 else c2) - b * x)
        total += math.log(max(upper - lower, _TINY))
    return total


def _gradient_hessian(
    theta: Sequence[float], xs: Sequence[float], ks: Sequence[int]
) -> tuple[list[float], list[list[float]]]:
    """Gradient and Hessian of the log-likelihood in ``theta = (c1, c2, b)``.

    For one match, ``log p = log(F(u) - F(l))`` with ``u`` the upper and ``l`` the lower linear
    predictor (``c_k - b*x``; +inf/-inf at the ends). Derivatives in ``u`` and ``l`` are chained
    through ``du/dtheta`` and ``dl/dtheta``, each 1 on its cut-point and ``-x`` on ``b``."""
    c1, c2, b = theta
    cuts = (c1, c2)
    g = [0.0, 0.0, 0.0]
    h = [[0.0] * 3 for _ in range(3)]
    for x, k in zip(xs, ks):
        has_upper, has_lower = k < 2, k > 0
        f_u = _sigmoid(cuts[k] - b * x) if has_upper else 1.0
        f_l = _sigmoid(cuts[k - 1] - b * x) if has_lower else 0.0
        p = max(f_u - f_l, _TINY)
        d_u = f_u * (1.0 - f_u)  # logistic density at u (0 at +inf)
        d_l = f_l * (1.0 - f_l)
        g_u = d_u / p
        g_l = -d_l / p
        h_uu = d_u * (1.0 - 2.0 * f_u) / p - g_u * g_u
        h_ll = -d_l * (1.0 - 2.0 * f_l) / p - g_l * g_l
        h_ul = -g_u * g_l
        du = [0.0, 0.0, -x] if has_upper else [0.0, 0.0, 0.0]
        dl = [0.0, 0.0, -x] if has_lower else [0.0, 0.0, 0.0]
        if has_upper:
            du[k] = 1.0
        if has_lower:
            dl[k - 1] = 1.0
        for i in range(3):
            g[i] += g_u * du[i] + g_l * dl[i]
            for j in range(3):
                h[i][j] += (
                    h_uu * du[i] * du[j]
                    + h_ll * dl[i] * dl[j]
                    + h_ul * (du[i] * dl[j] + dl[i] * du[j])
                )
    return g, h


def _solve3(a: list[list[float]], v: list[float]) -> list[float]:
    """Solve the 3x3 system ``a @ s = v`` by Gaussian elimination with partial pivoting."""
    m = [row[:] + [v[i]] for i, row in enumerate(a)]
    scale = max(abs(e) for row in a for e in row) or 1.0
    for col in range(3):
        pivot = max(range(col, 3), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) <= 1e-12 * scale:
            raise OrderedLogitError("singular Hessian: the data don't identify the parameters")
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(col + 1, 3):
            factor = m[r][col] / m[col][col]
            for c in range(col, 4):
                m[r][c] -= factor * m[col][c]
    s = [0.0, 0.0, 0.0]
    for r in (2, 1, 0):
        s[r] = (m[r][3] - sum(m[r][c] * s[c] for c in range(r + 1, 3))) / m[r][r]
    return s


def fit_ordered_logit(
    xs: Sequence[float],
    results: Sequence[str],
    *,
    start: OrderedLogit | None = None,
    max_iter: int = 100,
    tol: float = 1e-9,
) -> OrderedLogit:
    """Maximum-likelihood ordered logit of ``results`` (``"H"``/``"D"``/``"A"``) on ``xs``.

    Newton's method with step halving, from ``start`` or from the outcome frequencies with a zero
    slope. Raises ``OrderedLogitError`` if an outcome never occurs (the maximum doesn't exist),
    the parameters aren't identified (e.g. every ``x`` equal), or it fails to converge."""
    if len(xs) != len(results):
        raise ValueError("xs and results must have the same length")
    ks = [_CATEGORY[r] for r in results]
    counts = [ks.count(c) for c in range(3)]
    if min(counts) == 0:
        raise OrderedLogitError(f"every outcome must occur at least once, got {counts}")
    if start is None:
        n = len(ks)
        p_away, p_away_or_draw = counts[0] / n, (counts[0] + counts[1]) / n
        theta = [
            math.log(p_away / (1 - p_away)),
            math.log(p_away_or_draw / (1 - p_away_or_draw)),
            0.0,
        ]
    else:
        theta = [start.cut_away, start.cut_draw, start.slope]
    ll = _log_likelihood(theta, xs, ks)
    for _ in range(max_iter):
        g, h = _gradient_hessian(theta, xs, ks)
        step = _solve3(h, g)  # Newton: theta - H^-1 g (H is negative definite)
        t = 1.0
        while True:
            candidate = [theta[i] - t * step[i] for i in range(3)]
            if candidate[0] < candidate[1]:
                cand_ll = _log_likelihood(candidate, xs, ks)
                if cand_ll >= ll - 1e-12 * max(1.0, abs(ll)):
                    break
            t /= 2.0
            if t < 1e-12:
                raise OrderedLogitError("line search failed")
        theta, ll = candidate, cand_ll
        if max(abs(t * s) for s in step) < tol:
            return OrderedLogit(theta[0], theta[1], theta[2])
    raise OrderedLogitError(f"no convergence in {max_iter} Newton iterations")


# --- Model --------------------------------------------------------------------------------------


class EloModel:
    """Elo ratings plus an ordered-logit 1X2 mapping; implements the backtest ``Model``."""

    name = "elo"

    def __init__(self, params: EloParams | None = None, *, min_fit_matches: int = 100) -> None:
        self.params = params or EloParams()
        self.min_fit_matches = min_fit_matches
        """Below this many history matches, ``DEFAULT_LOGIT`` is used instead of a fit."""
        self.logit: OrderedLogit = DEFAULT_LOGIT
        """The 1X2 mapping from the last ``fit``."""
        self.as_of: date | None = None
        self._reset()

    def _reset(self) -> None:
        self._engine = EloRatings(self.params)
        self._processed: list[MatchRecord] = []
        self._xs: list[float] = []
        self._results: list[str] = []
        self._last_fit: OrderedLogit | None = None

    def _shares_prefix(self, history: Sequence[MatchRecord]) -> bool:
        n = len(self._processed)
        if len(history) < n:
            return False
        return all(a is b or a == b for a, b in zip(history, self._processed))

    def fit(self, history: Sequence[MatchRecord], as_of: date) -> None:
        """Rebuild ratings from ``history`` (played matches before ``as_of``, oldest first) and
        fit the ordered logit on it. Work over an unchanged history prefix is reused."""
        if not self._shares_prefix(history):
            self._reset()
        for record in history[len(self._processed) :]:
            self._xs.append(self._engine.process(record))
            self._results.append(record.result)  # type: ignore[arg-type]
            self._processed.append(record)
        self.as_of = as_of
        self.logit = DEFAULT_LOGIT
        if len(self._xs) >= self.min_fit_matches:
            try:
                self._last_fit = fit_ordered_logit(self._xs, self._results, start=self._last_fit)
            except OrderedLogitError:
                try:  # a bad warm start shouldn't matter: retry from the frequencies
                    self._last_fit = fit_ordered_logit(self._xs, self._results)
                except OrderedLogitError:
                    self._last_fit = None
            if self._last_fit is not None:
                self.logit = self._last_fit

    def rating(self, team: str, season: str | None = None) -> float:
        """``team``'s rating for a match in ``season`` (default: the latest season in history)."""
        return self._engine.rating(team, season)

    def ratings(self, season: str | None = None) -> dict[str, float]:
        """Every known team's rating as of the last ``fit`` (spec: ``team_ratings.elo``).

        With ``season`` later than the history's last season, the start-of-season ratings
        (regressed; promoted teams aren't listed, see ``promoted_rating``)."""
        return self._engine.ratings_for(season)

    def promoted_rating(self, season: str | None = None) -> float:
        """Starting rating of a team new to ``season`` (default: the latest season)."""
        if season is None or season == self._engine.season:
            return self._engine.promoted_rating
        return self._engine.next_season_start()[1]

    def probabilities(self, home_rating: float, away_rating: float) -> Probabilities:
        """(p_home, p_draw, p_away) for two ratings, with home advantage and the fitted logit."""
        return self.logit.probabilities(
            elo_difference(home_rating, away_rating, self.params.home_advantage)
        )

    def predict(self, match: MatchView) -> Probabilities:
        return self.probabilities(
            self.rating(match.home_team, match.season), self.rating(match.away_team, match.season)
        )

