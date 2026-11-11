"""Inputs and outputs of the market-scoring workflow and activity.

Plain dataclasses only: the workflow imports this module, so nothing here may do I/O or read the
clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..ingest.models import DIVISION

SCORE_MARKET_ACTIVITY = "football_score_market"


@dataclass
class MarketScoreRequest:
    """What one ``MarketScoreWorkflow`` run scores.

    ``seasons`` empty means the current season (by the workflow's clock), which is what a run after
    the daily ingest needs.
    """

    seasons: list[str] = field(default_factory=list)
    division: str = DIVISION


@dataclass
class SeasonRef:
    """One season to score."""

    season: str
    division: str = DIVISION


@dataclass
class MarketScoreResult:
    """What scoring one season did. On a re-run of unchanged data ``inserted``/``updated``/``deleted`` are 0."""

    season: str
    matches: int = 0
    """Matches of the season in the database."""
    matches_scored: int = 0
    """Finished matches with at least one usable 1X2 quote."""
    rows: int = 0
    """market_scores rows computed (and sent) for the season."""
    quotes_skipped: int = 0
    """Stored quotes not scored because a 1X2 price was missing or <= 1.0."""
    inserted: int = 0
    updated: int = 0
    deleted: int = 0
    mean_rps: dict[str, float] = field(default_factory=dict)
    """Mean RPS per ``<bookmaker or consensus>/<snapshot>``, e.g. ``consensus/pre_match``."""


@dataclass
class MarketScoreSummary:
    """Each season's result for one workflow run."""

    seasons: list[MarketScoreResult] = field(default_factory=list)

    def total(self, name: str) -> int:
        return sum(getattr(result, name) for result in self.seasons)
