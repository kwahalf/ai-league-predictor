"""Walk-forward backtest over parsed season files. Pure: records in, results out, no I/O.

Spec, "Backtest (walk-forward)": for each week after a warm-up season, refit using only matches
before that week, predict that week using pre-match odds, then move on.

**Week boundary.** A window is one calendar week of ``match_date``, Monday to Sunday.
``match_date`` is the UK-local date as published (the files have no time before 2019/20), so a
window starts at Monday 00:00 UK time (Europe/London). The model is fitted with ``as_of`` = that
Monday and sees only matches with ``match_date < as_of``. Every match in the window is predicted
from the same fit, so no result from the window (in particular none from the same date as a
predicted match) is visible to it.

**Seasons.** Matches of the warm-up seasons (default 2016/17) are history only and never
predicted. Every match of every other season is predicted; matches without a result are reported
as predicted but not scored.

**Market benchmark.** For each predicted match, the equal-weight consensus of the margin-free
pre-match 1X2 odds of ``B365``, ``BW`` and ``AVG`` (``market.market_consensus``). It is scored on
the matches that have it, alongside the model on that same subset; matches without it are counted.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import groupby
from typing import Iterable, Sequence

from ..data import MatchRecord
from ..metrics import (
    AWAY,
    DRAW,
    HOME,
    PROB_SUM_TOLERANCE,
    CalibrationBucket,
    brier,
    calibration_buckets,
    log_loss,
    rps,
)
from .inputs import history_record, match_view, model_input_odds
from .market import market_consensus
from .model import Model, Probabilities

DEFAULT_WARMUP_SEASONS: tuple[str, ...] = ("1617",)
_OUTCOME_INDEX = {"H": HOME, "D": DRAW, "A": AWAY}


class BacktestError(ValueError):
    """Bad input to the backtest, or a model returned an invalid prediction."""


def week_start(day: date) -> date:
    """The Monday of ``day``'s week: the start of the backtest window containing ``day``."""
    return day - timedelta(days=day.weekday())


@dataclass(frozen=True)
class MatchPrediction:
    season: str
    match_date: date
    kickoff_at: datetime | None
    home_team: str
    away_team: str
    window_start: date
    model: Probabilities
    market: Probabilities | None
    """Market consensus, or ``None`` when no model-input source had usable pre-match 1X2 odds."""
    outcome: int | None
    """0 home, 1 draw, 2 away; ``None`` if the match has no result."""


@dataclass(frozen=True)
class Scores:
    n: int
    rps: float
    log_loss: float
    brier: float


@dataclass(frozen=True)
class Summary:
    """Scores for one season, or overall (``season == "all"``)."""

    season: str
    n_predicted: int
    n_scored: int
    """Predicted matches with a result."""
    model: Scores | None
    """The model on every scored match."""
    market: Scores | None
    """The market benchmark on scored matches that have one."""
    model_on_market_matches: Scores | None
    """The model on exactly the matches ``market`` is scored on, for a like-for-like comparison."""
    n_without_market: int
    """Scored matches with no usable pre-match odds, hence no market benchmark."""


@dataclass(frozen=True)
class BacktestResult:
    model_name: str
    warmup_seasons: tuple[str, ...]
    n_windows: int
    predictions: tuple[MatchPrediction, ...]
    seasons: tuple[Summary, ...]
    overall: Summary
    model_calibration: tuple[CalibrationBucket, ...]
    market_calibration: tuple[CalibrationBucket, ...]


def _sort_key(record: MatchRecord) -> tuple:
    return (
        record.match_date,
        record.kickoff_at is not None,
        record.kickoff_at or datetime.min,
        record.home_team,
        record.away_team,
    )


def _check_prediction(probs: object, model_name: str, record: MatchRecord) -> Probabilities:
    where = f"model {model_name!r} on {record.match_date} {record.home_team} v {record.away_team}"
    try:
        values = tuple(float(p) for p in probs)  # type: ignore[union-attr]
    except (TypeError, ValueError) as exc:
        raise BacktestError(f"{where}: prediction must be 3 numbers, got {probs!r}") from exc
    if len(values) != 3:
        raise BacktestError(f"{where}: prediction must be 3 numbers, got {probs!r}")
    if any(not math.isfinite(p) or p < 0.0 or p > 1.0 for p in values):
        raise BacktestError(f"{where}: probabilities must be in [0, 1], got {values!r}")
    if abs(math.fsum(values) - 1.0) > PROB_SUM_TOLERANCE:
        raise BacktestError(f"{where}: probabilities must sum to 1, got {values!r}")
    return (values[0], values[1], values[2])


def run_backtest(
    records: Iterable[MatchRecord],
    model: Model,
    *,
    warmup_seasons: Sequence[str] = DEFAULT_WARMUP_SEASONS,
    n_buckets: int = 10,
) -> BacktestResult:
    """Walk forward week by week over ``records`` (all seasons, any order), refitting ``model``
    before each window. See the module docstring for the rules."""
    warmup = tuple(warmup_seasons)
    ordered = sorted(records, key=_sort_key)
    to_predict = [r for r in ordered if r.season not in warmup]
    if not to_predict:
        raise BacktestError(f"no matches outside the warm-up seasons {warmup}")

    # History is only ever matches with a result, odds cut down to the model inputs.
    played = [r for r in ordered if r.result is not None]
    history = [history_record(r) for r in played]
    history_dates = [r.match_date for r in played]

    predictions: list[MatchPrediction] = []
    n_windows = 0
    for start, window in groupby(to_predict, key=lambda r: week_start(r.match_date)):
        n_windows += 1
        visible = tuple(history[: bisect_left(history_dates, start)])
        model.fit(visible, start)
        for record in window:
            probs = _check_prediction(model.predict(match_view(record)), model.name, record)
            predictions.append(
                MatchPrediction(
                    season=record.season,
                    match_date=record.match_date,
                    kickoff_at=record.kickoff_at,
                    home_team=record.home_team,
                    away_team=record.away_team,
                    window_start=start,
                    model=probs,
                    market=market_consensus(model_input_odds(record.odds)),
                    outcome=_OUTCOME_INDEX.get(record.result) if record.result else None,
                )
            )

    seasons = tuple(
        summarise(season, list(group))
        for season, group in groupby(
            sorted(predictions, key=lambda p: p.season), key=lambda p: p.season
        )
    )
    scored = [p for p in predictions if p.outcome is not None]
    return BacktestResult(
        model_name=model.name,
        warmup_seasons=warmup,
        n_windows=n_windows,
        predictions=tuple(predictions),
        seasons=seasons,
        overall=summarise("all", predictions),
        model_calibration=tuple(
            calibration_buckets(((p.model, p.outcome) for p in scored), n_buckets)
        ),
        market_calibration=tuple(
            calibration_buckets(
                ((p.market, p.outcome) for p in scored if p.market is not None), n_buckets
            )
        ),
    )


def _scores(pairs: list[tuple[Probabilities, int]]) -> Scores | None:
    if not pairs:
        return None
    n = len(pairs)
    return Scores(
        n=n,
        rps=math.fsum(rps(p, o) for p, o in pairs) / n,
        log_loss=math.fsum(log_loss(p, o) for p, o in pairs) / n,
        brier=math.fsum(brier(p, o) for p, o in pairs) / n,
    )


def summarise(season: str, predictions: Sequence[MatchPrediction]) -> Summary:
    """Score ``predictions`` (model and market) with the 2.1a metrics."""
    scored = [p for p in predictions if p.outcome is not None]
    with_market = [p for p in scored if p.market is not None]
    return Summary(
        season=season,
        n_predicted=len(predictions),
        n_scored=len(scored),
        model=_scores([(p.model, p.outcome) for p in scored]),  # type: ignore[misc]
        market=_scores([(p.market, p.outcome) for p in with_market]),  # type: ignore[misc]
        model_on_market_matches=_scores(
            [(p.model, p.outcome) for p in with_market]  # type: ignore[misc]
        ),
        n_without_market=len(scored) - len(with_market),
    )
