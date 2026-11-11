"""Turn parsed ``MatchRecord`` objects into the JSON the ``football_ingest_matches`` RPC takes.

Pure functions, no I/O. Two cleaning rules live here:

- **Prices <= 1.0 are stored as missing** (owner decision 2026-11-11), judged at the database's
  precision (3 decimal places), so 1.0004, stored as 1.000, counts too. Decimal odds of 1.0 or less
  are impossible, and the real 2021/22 file has Bet365 closing over/under prices of ``0`` for
  Newcastle v Man City on 19/12/2021. Each dropped price is counted so the ingest can log it.
- **Prices come in complete sets.** The schema requires home/draw/away to be all present or all
  missing, and the same for over/under 2.5. A set left incomplete (by a dropped price or a blank
  cell in the source) is nulled as a whole and counted. A quote with no prices left is not sent.

Kick-off: season files before 2019/20 have no ``Time`` column, so ``kickoff_at`` is ``None``; the
match is then stored at 00:00 UTC on its (UK) date, which keeps the date right in both UTC and UK
time. ``matches.kickoff_at`` is not nullable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable

from ..data.models import MatchRecord, OddsQuote
from ..data.teams import TEAM_ALIASES

STAT_FIELDS = (
    "home_shots",
    "away_shots",
    "home_shots_on_target",
    "away_shots_on_target",
    "home_corners",
    "away_corners",
    "home_yellow_cards",
    "away_yellow_cards",
    "home_red_cards",
    "away_red_cards",
)
# odds prices are numeric(8, 3) (core migration).
PRICE_PRECISION = Decimal("0.001")
ONE_X_TWO = ("home", "draw", "away")
OVER_UNDER = ("over_2_5", "under_2_5")


class DuplicateMatchError(ValueError):
    """The same fixture appears more than once in one file."""


@dataclass
class CleanedQuote:
    prices: dict[str, float | None]
    prices_dropped: int
    sets_nulled: int

    @property
    def is_empty(self) -> bool:
        return all(value is None for value in self.prices.values())


def _stored(price: float) -> Decimal:
    """``price`` as Postgres stores it in ``numeric(8, 3)``: JSON sends ``repr(price)``, which
    Postgres rounds half away from zero to 3 places. So 1.0004 is stored as 1.000 and must count as
    <= 1.0, or the ``> 1.0`` constraint would reject the whole file."""
    return Decimal(repr(price)).quantize(PRICE_PRECISION, rounding=ROUND_HALF_UP)


def clean_quote(quote: OddsQuote) -> CleanedQuote:
    """Apply the two cleaning rules (module docstring) to one quote."""
    prices: dict[str, float | None] = {}
    dropped = 0
    for name in ONE_X_TWO + OVER_UNDER:
        value = getattr(quote, name)
        if value is not None and _stored(value) <= 1:
            value = None
            dropped += 1
        prices[name] = value
    nulled = 0
    for group in (ONE_X_TWO, OVER_UNDER):
        present = [prices[name] is not None for name in group]
        if any(present) and not all(present):
            for name in group:
                prices[name] = None
            nulled += 1
    return CleanedQuote(prices, dropped, nulled)


def kickoff_iso(record: MatchRecord) -> str:
    """UTC kick-off as ISO 8601; 00:00 UTC on the match date when the file has no time."""
    kickoff = record.kickoff_at or datetime.combine(record.match_date, time(0), tzinfo=timezone.utc)
    return kickoff.astimezone(timezone.utc).isoformat()


def match_status(record: MatchRecord) -> str:
    return "finished" if record.home_goals is not None and record.away_goals is not None else "scheduled"


@dataclass
class Payload:
    teams: list[dict[str, Any]]
    matches: list[dict[str, Any]]
    prices_dropped: int
    price_sets_nulled: int


def build_payload(records: Iterable[MatchRecord], *, include_stats: bool) -> Payload:
    """Teams (canonical name and aliases) and matches (with stats and cleaned odds) for the RPC.

    Raises ``DuplicateMatchError`` if a fixture (season, home, away) appears twice: the database
    has one row per fixture, so a file listing one twice is wrong and must not be loaded.
    """
    records = list(records)
    seen: set[tuple[str, str, str]] = set()
    for record in records:
        key = (record.season, record.home_team, record.away_team)
        if key in seen:
            raise DuplicateMatchError(
                f"{record.home_team} v {record.away_team} appears twice in {record.season}"
            )
        seen.add(key)
    team_names = sorted({name for r in records for name in (r.home_team, r.away_team)})
    teams = [{"name": name, "aliases": sorted(set(TEAM_ALIASES.get(name, ())))} for name in team_names]

    matches: list[dict[str, Any]] = []
    dropped = nulled = 0
    for record in records:
        odds = []
        for quote in record.odds:
            cleaned = clean_quote(quote)
            dropped += cleaned.prices_dropped
            nulled += cleaned.sets_nulled
            if not cleaned.is_empty:
                odds.append({"source": quote.source, "snapshot": quote.snapshot, **cleaned.prices})
        stats = None
        if include_stats:
            values = {name: getattr(record, name) for name in STAT_FIELDS}
            if any(value is not None for value in values.values()):
                stats = values
        matches.append(
            {
                "season": record.season,
                "home_team": record.home_team,
                "away_team": record.away_team,
                "kickoff_at": kickoff_iso(record),
                "home_goals": record.home_goals,
                "away_goals": record.away_goals,
                "status": match_status(record),
                "stats": stats,
                "odds": odds,
            }
        )
    return Payload(teams, matches, dropped, nulled)
