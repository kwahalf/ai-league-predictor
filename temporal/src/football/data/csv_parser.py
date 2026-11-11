"""Parse football-data.co.uk E0 CSV files into ``MatchRecord`` objects. No I/O: text or bytes in.

Source files:
- Season files ``https://www.football-data.co.uk/mmz4281/{season}/E0.csv`` -> ``parse_season_csv``.
- Upcoming fixtures ``https://www.football-data.co.uk/fixtures.csv`` (all leagues; filtered to one
  division) -> ``parse_fixtures_csv``. Its odds are snapshot ``live``.

Format details handled here:
- Optional UTF-8 byte-order mark (present in some seasons and in ``fixtures.csv``).
- ``Date`` as ``dd/mm/yy`` (2016/17) or ``dd/mm/yyyy`` (later files).
- ``Time`` missing before 2019/20: ``kickoff_at`` is then ``None``, ``match_date`` is still set.
- ``Date``/``Time`` are UK local time (``Europe/London``, GMT/BST). notes.txt doesn't state a
  timezone; it was checked against known kick-offs, e.g. Man City v Arsenal on 31/03/2024 (the
  day BST began) is listed at 16:30, its real UK kick-off, which is 15:30 UTC.
- Trailing empty rows and unnamed trailing columns are ignored.
"""

from __future__ import annotations

import csv
import io
import math
import re
from datetime import date, datetime, time, timezone
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

from .columns import ODDS_COLUMNS, OddsColumns, snapshot_for
from .models import MatchRecord, OddsQuote, Result
from .teams import canonical_team

SOURCE_TIMEZONE = ZoneInfo("Europe/London")
DEFAULT_DIVISION = "E0"

_SEASON_RE = re.compile(r"^\d{4}$")
_REQUIRED_COLUMNS = ("Div", "Date", "HomeTeam", "AwayTeam")
_STAT_COLUMNS = {
    "home_shots": "HS",
    "away_shots": "AS",
    "home_shots_on_target": "HST",
    "away_shots_on_target": "AST",
    "home_corners": "HC",
    "away_corners": "AC",
    "home_yellow_cards": "HY",
    "away_yellow_cards": "AY",
    "home_red_cards": "HR",
    "away_red_cards": "AR",
}


class CsvFormatError(ValueError):
    """The file isn't a layout we understand, or a value in it can't be parsed."""


class UnknownLayoutError(CsvFormatError):
    """The file has none of the recognised 1X2 odds columns, or lacks the basic match columns."""


def parse_season_csv(
    data: str | bytes, season: str, *, division: str = DEFAULT_DIVISION
) -> list[MatchRecord]:
    """Parse one season file (e.g. ``mmz4281/2324/E0.csv``) for ``season`` (e.g. ``"2324"``).

    Odds come out as snapshots ``pre_match`` and ``closing``. Every row must belong to
    ``division``: a row from another division means the wrong file was passed, and raises.
    """
    return _parse(data, season, division=division, live=False)


def parse_fixtures_csv(
    data: str | bytes, season: str, *, division: str = DEFAULT_DIVISION
) -> list[MatchRecord]:
    """Parse ``fixtures.csv``, keeping only rows of ``division``. Odds come out as snapshot ``live``.

    ``season`` is the season the fixtures belong to (the caller knows the current season; it can't
    be derived reliably from dates, e.g. 2019/20 ran into July 2020).
    """
    return _parse(data, season, division=division, live=True)


def _parse(data: str | bytes, season: str, *, division: str, live: bool) -> list[MatchRecord]:
    if not _SEASON_RE.match(season):
        raise ValueError(f"season must look like '2324', got {season!r}")
    reader = csv.DictReader(io.StringIO(_decode(data)))
    header = [name.strip() for name in (reader.fieldnames or []) if name and name.strip()]
    reader.fieldnames = [name.strip() if name else "" for name in (reader.fieldnames or [])]
    _check_layout(header)
    odds_layout = _resolve_odds_columns(header, live=live)

    matches: list[MatchRecord] = []
    for row in reader:
        line_no = reader.line_num
        if _is_blank(row):
            continue
        if any(str(v).strip() for v in row.get(None) or ()):  # type: ignore[call-overload]
            raise CsvFormatError(f"line {line_no}: more values than header columns")
        row_division = (row.get("Div") or "").strip()
        if row_division != division:
            if live:
                continue
            raise CsvFormatError(
                f"line {line_no}: division {row_division!r} in a {division} season file"
            )
        try:
            matches.append(_parse_row(row, season, odds_layout))
        except CsvFormatError as exc:
            raise CsvFormatError(f"line {line_no}: {exc}") from exc
    return matches


def _decode(data: str | bytes) -> str:
    if isinstance(data, bytes):
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError:
            # Older football-data files have been Windows-1252; latin-1 never fails.
            return data.decode("latin-1")
    return data.removeprefix("\ufeff")


def _is_blank(row: Mapping[str | None, object]) -> bool:
    for key, value in row.items():
        if key is None:  # surplus fields beyond the header
            values = value if isinstance(value, list) else [value]
            if any(str(v).strip() for v in values):
                return False
        elif value is not None and str(value).strip():
            return False
    return True


def _check_layout(header: list[str]) -> None:
    missing = [name for name in _REQUIRED_COLUMNS if name not in header]
    if missing:
        raise UnknownLayoutError(f"missing required columns {missing}; header starts {header[:8]}")
    if not any(col in header for group in ODDS_COLUMNS for col in group.one_x_two_columns()):
        raise UnknownLayoutError(
            "no recognised 1X2 odds columns (B365H, BWH, AvgH, BbAvH, PSH or closing variants); "
            f"header starts {header[:8]}"
        )


_ResolvedOdds = list[tuple[OddsColumns, str, tuple[str | None, ...]]]


def _resolve_odds_columns(header: list[str], *, live: bool) -> _ResolvedOdds:
    """For each column group used by this kind of file, pick the column present for each price."""
    present = set(header)
    resolved: _ResolvedOdds = []
    for group in ODDS_COLUMNS:
        snapshot = snapshot_for(group, live=live)
        if snapshot is None:
            continue
        picks = tuple(
            _first_present(candidates, present)
            for candidates in (group.home, group.draw, group.away, group.over_2_5, group.under_2_5)
        )
        if any(picks):
            resolved.append((group, snapshot, picks))
    return resolved


def _first_present(candidates: Iterable[str], present: set[str]) -> str | None:
    return next((name for name in candidates if name in present), None)


def _parse_row(row: Mapping[str, str], season: str, odds_layout: _ResolvedOdds) -> MatchRecord:
    match_date = _parse_date(_text(row, "Date"))
    kickoff_text = _text(row, "Time")
    kickoff_at = _to_utc(match_date, _parse_time(kickoff_text)) if kickoff_text else None

    home_goals = _int(row, "FTHG")
    away_goals = _int(row, "FTAG")
    result_text = _text(row, "FTR")
    if result_text and result_text not in ("H", "D", "A"):
        raise CsvFormatError(f"FTR must be H, D or A, got {result_text!r}")
    result: Result | None = result_text or None  # type: ignore[assignment]

    odds: list[OddsQuote] = []
    for group, snapshot, picks in odds_layout:
        prices = tuple(_float(row, column) if column else None for column in picks)
        if any(price is not None for price in prices):
            odds.append(OddsQuote(group.source, snapshot, *prices))  # type: ignore[arg-type]

    return MatchRecord(
        season=season,
        match_date=match_date,
        kickoff_at=kickoff_at,
        home_team=canonical_team(_text(row, "HomeTeam")),
        away_team=canonical_team(_text(row, "AwayTeam")),
        home_goals=home_goals,
        away_goals=away_goals,
        result=result,
        odds=tuple(odds),
        **{field_name: _int(row, column) for field_name, column in _STAT_COLUMNS.items()},
    )


def _text(row: Mapping[str, str], column: str) -> str:
    return (row.get(column) or "").strip()


def _int(row: Mapping[str, str], column: str) -> int | None:
    text = _text(row, column)
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        raise CsvFormatError(f"{column} must be a whole number, got {text!r}") from None


def _float(row: Mapping[str, str], column: str) -> float | None:
    text = _text(row, column)
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise CsvFormatError(f"{column} must be a number, got {text!r}") from None
    if not math.isfinite(value):
        raise CsvFormatError(f"{column} must be a finite number, got {text!r}")
    # Prices <= 1.0 are left for the data-quality checks (plan step 1.4) to flag.
    return value


def _parse_date(text: str) -> date:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise CsvFormatError(f"Date must be dd/mm/yy or dd/mm/yyyy, got {text!r}")


def _parse_time(text: str) -> time:
    try:
        return datetime.strptime(text, "%H:%M").time()
    except ValueError:
        raise CsvFormatError(f"Time must be HH:MM, got {text!r}") from None


def _to_utc(day: date, local_time: time) -> datetime:
    """UK local date and time -> timezone-aware UTC datetime."""
    return datetime.combine(day, local_time, tzinfo=SOURCE_TIMEZONE).astimezone(timezone.utc)
