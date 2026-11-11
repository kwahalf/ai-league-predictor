"""Inputs and outputs of the ingest workflow and its activities, plus season arithmetic.

Plain dataclasses and pure functions only: the workflow imports this module, so nothing here may do
I/O or read the clock.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

DIVISION = "E0"
# First season in the history (spec "Decisions": 2016/17 to 2025/26 plus the current season).
FIRST_SEASON = "1617"
# The season rolls over on 1 July: fixtures.csv only lists the next week or so of matches, and the
# EPL never plays in July, so a date-based cut-off is unambiguous for it.
SEASON_START_MONTH = 7

# Activity names, shared by the workflow (which calls them by name) and the activities.
FETCH_ACTIVITY = "football_fetch_csv"
LOAD_ACTIVITY = "football_load_csv"

SEASON_KIND = "season"
FIXTURES_KIND = "fixtures"

_SEASON_RE = re.compile(r"^\d{4}$")


def validate_season(season: str) -> str:
    """Return ``season`` if it looks like ``'2324'`` (two consecutive years), else raise."""
    if not isinstance(season, str) or not _SEASON_RE.match(season):
        raise ValueError(f"season must look like '2324', got {season!r}")
    if (int(season[:2]) + 1) % 100 != int(season[2:]):
        raise ValueError(f"season {season!r} is not two consecutive years")
    return season


def season_for(day: date) -> str:
    """Football-data season code for the season running on ``day``, e.g. 2026-11-11 -> ``'2627'``."""
    start = day.year if day.month >= SEASON_START_MONTH else day.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def season_range(first: str, last: str) -> list[str]:
    """Every season from ``first`` to ``last`` inclusive, e.g. ``('1617', '1819')`` -> 3 seasons."""
    validate_season(first)
    validate_season(last)
    start, end = _start_year(first), _start_year(last)
    if end < start:
        raise ValueError(f"season range {first}..{last} is backwards")
    return [f"{y % 100:02d}{(y + 1) % 100:02d}" for y in range(start, end + 1)]


def _start_year(season: str) -> int:
    # Two-digit years: 00-69 are 2000s, 70-99 are 1900s (football-data's files start in 1993/94).
    yy = int(season[:2])
    return 2000 + yy if yy < 70 else 1900 + yy


@dataclass
class IngestRequest:
    """What one ``IngestWorkflow`` run loads.

    ``seasons`` empty means the current season (by the workflow's clock), which is what the daily
    schedule sends. ``include_fixtures`` also loads ``fixtures.csv`` for the current season, after
    the season files, so a match that has a result is never turned back into a fixture.
    """

    seasons: list[str] = field(default_factory=list)
    include_fixtures: bool = True
    division: str = DIVISION


@dataclass
class CsvSource:
    """One file to download: a season file (``kind='season'``) or ``fixtures.csv``."""

    kind: str
    season: str
    division: str = DIVISION


@dataclass
class RawCsvRef:
    """A downloaded file, as stored in Supabase Storage."""

    kind: str
    season: str
    division: str
    url: str
    storage_path: str
    sha256: str
    size_bytes: int
    # ISO 8601 UTC time of the download. Becomes odds.captured_at for new or changed prices.
    downloaded_at: str


@dataclass
class LoadResult:
    """What loading one file did. On a re-run of unchanged data every ``*_inserted``/``*_updated``/``*_deleted`` is 0."""

    kind: str
    season: str
    storage_path: str
    matches_in_file: int = 0
    teams_inserted: int = 0
    teams_updated: int = 0
    matches_inserted: int = 0
    matches_updated: int = 0
    stats_inserted: int = 0
    stats_updated: int = 0
    # Stored stats rows of matches whose statistics the file now leaves all blank.
    stats_deleted: int = 0
    odds_inserted: int = 0
    odds_updated: int = 0
    # Stored quotes the file no longer has (absent, or nothing left after cleaning).
    odds_deleted: int = 0
    # 1 if the load was skipped because a newer download of the same file was already loaded
    # (overlapping manual and scheduled runs).
    stale_skipped: int = 0
    # fixtures.csv rows left alone: the match already has a result, or kick-off has passed.
    fixtures_skipped: int = 0
    # Prices <= 1.0 stored as missing (owner decision 2026-11-11), and the 1X2 or over/under sets
    # nulled because a dropped or absent price left them incomplete.
    prices_dropped: int = 0
    price_sets_nulled: int = 0


@dataclass
class IngestSummary:
    """Totals for one workflow run, plus each file's result."""

    files: list[LoadResult] = field(default_factory=list)

    def total(self, name: str) -> int:
        return sum(getattr(result, name) for result in self.files)
