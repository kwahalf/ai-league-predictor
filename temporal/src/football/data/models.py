"""Canonical shapes produced by the football-data.co.uk CSV parser.

These mirror the spec's ``matches`` and ``odds`` tables (docs/specs/2026-11-11-match-predictor.md,
"Data Model"), minus database ids: teams are canonical names, bookmakers are source codes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

SourceCode = Literal["B365", "BW", "AVG", "PS"]
Snapshot = Literal["pre_match", "closing", "live"]
Result = Literal["H", "D", "A"]


@dataclass(frozen=True)
class OddsQuote:
    """One bookmaker's prices for one match at one snapshot. Missing prices are ``None``."""

    source: SourceCode
    snapshot: Snapshot
    home: float | None
    draw: float | None
    away: float | None
    over_2_5: float | None
    under_2_5: float | None


@dataclass(frozen=True)
class MatchRecord:
    """One match row from a season file or from ``fixtures.csv``.

    ``match_date`` is the date as published (UK local). ``kickoff_at`` is the kick-off converted to
    UTC (timezone-aware), or ``None`` when the file has no ``Time`` column (seasons before 2019/20).
    Goals, result and stats are ``None`` when absent (always, for upcoming fixtures).
    """

    season: str
    match_date: date
    kickoff_at: datetime | None
    home_team: str
    away_team: str
    home_goals: int | None = None
    away_goals: int | None = None
    result: Result | None = None
    home_shots: int | None = None
    away_shots: int | None = None
    home_shots_on_target: int | None = None
    away_shots_on_target: int | None = None
    home_corners: int | None = None
    away_corners: int | None = None
    home_yellow_cards: int | None = None
    away_yellow_cards: int | None = None
    home_red_cards: int | None = None
    away_red_cards: int | None = None
    odds: tuple[OddsQuote, ...] = field(default_factory=tuple)

    def odds_for(self, source: SourceCode, snapshot: Snapshot) -> OddsQuote | None:
        """Return the quote for ``source`` at ``snapshot``, or ``None`` if the file had none."""
        for quote in self.odds:
            if quote.source == source and quote.snapshot == snapshot:
                return quote
        return None
