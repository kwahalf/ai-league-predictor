"""What a model is allowed to see: the leakage boundary of the backtest.

Spec rule (docs/specs/2026-11-11-match-predictor.md, "Odds data"): models may only use odds that
existed when the prediction was made. In the backtest that means **pre-match odds only**, from the
model-input sources ``B365``, ``BW`` and ``AVG``. Closing odds and Pinnacle (``PS``, benchmark
only) never reach a model.

Two shapes cross the boundary:

- ``MatchView`` for a match being predicted: teams, date/kick-off, season and pre-match odds. It
  has no goals, result, stats or closing odds, so a model *cannot* read them: they aren't there.
- ``history_record`` for a match already played before the prediction window: the full
  ``MatchRecord`` (result and stats are what models learn from), with its odds cut down to the
  same pre-match model-input quotes.

Prices of 1.0 or less occur in the real files (e.g. a ``0`` in 2021/22); they are not real decimal
odds, so both shapes replace them with ``None`` ("missing").
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

from ..data import MatchRecord, OddsQuote

MODEL_ODDS_SOURCES: tuple[str, ...] = ("B365", "BW", "AVG")
"""Odds sources a model may use, in consensus order. Pinnacle ``PS`` is benchmark-only."""

MODEL_ODDS_SNAPSHOT = "pre_match"
"""The only odds snapshot a model may use in the backtest."""


@dataclass(frozen=True, slots=True)
class MatchView:
    """A match to predict, as the model sees it. Deliberately has no outcome fields.

    ``match_date`` is the UK-local date as published; ``kickoff_at`` is UTC, or ``None`` before
    2019/20. ``odds`` holds only pre-match quotes from ``MODEL_ODDS_SOURCES``, with prices <= 1.0
    replaced by ``None``.
    """

    season: str
    match_date: date
    kickoff_at: datetime | None
    home_team: str
    away_team: str
    odds: tuple[OddsQuote, ...] = ()

    def odds_for(self, source: str) -> OddsQuote | None:
        """The pre-match quote from ``source``, or ``None`` if there is none."""
        for quote in self.odds:
            if quote.source == source:
                return quote
        return None


def _clean_price(price: float | None) -> float | None:
    if price is None or not math.isfinite(price) or price <= 1.0:
        return None
    return price


def model_input_odds(quotes: Iterable[OddsQuote]) -> tuple[OddsQuote, ...]:
    """Keep pre-match quotes from the model-input sources; prices <= 1.0 become ``None``.

    A quote left with no usable price at all is dropped.
    """
    kept: list[OddsQuote] = []
    for quote in quotes:
        if quote.snapshot != MODEL_ODDS_SNAPSHOT or quote.source not in MODEL_ODDS_SOURCES:
            continue
        cleaned = OddsQuote(
            source=quote.source,
            snapshot=quote.snapshot,
            home=_clean_price(quote.home),
            draw=_clean_price(quote.draw),
            away=_clean_price(quote.away),
            over_2_5=_clean_price(quote.over_2_5),
            under_2_5=_clean_price(quote.under_2_5),
        )
        if any(
            p is not None
            for p in (cleaned.home, cleaned.draw, cleaned.away, cleaned.over_2_5, cleaned.under_2_5)
        ):
            kept.append(cleaned)
    return tuple(kept)


def match_view(record: MatchRecord) -> MatchView:
    """The restricted view of a match to predict: no goals, result, stats or closing odds."""
    return MatchView(
        season=record.season,
        match_date=record.match_date,
        kickoff_at=record.kickoff_at,
        home_team=record.home_team,
        away_team=record.away_team,
        odds=model_input_odds(record.odds),
    )


def history_record(record: MatchRecord) -> MatchRecord:
    """A played match as models see it in history: everything except non-input odds."""
    return dataclasses.replace(record, odds=model_input_odds(record.odds))
