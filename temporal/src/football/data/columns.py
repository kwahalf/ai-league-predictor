"""Mapping from football-data.co.uk odds columns to the canonical (source, snapshot) shape.

Column meanings are from https://www.football-data.co.uk/notes.txt; which columns exist in which
season is the coverage table in the spec ("Odds data"). Each canonical field lists the column names
that can carry it, newest naming first, and the first one present in a file's header wins:

- Market average: ``BbAv*`` up to 2018/19, ``Avg*`` from 2019/20; closing ``AvgC*``.
- Pinnacle over/under is ``P>2.5`` / ``PC>2.5`` (not ``PS``).
- Pre-2019/20 files have no closing columns except Pinnacle's ``PSC*``.

In ``fixtures.csv`` the pre-match columns hold the current prices, so they map to snapshot ``live``.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Snapshot, SourceCode


@dataclass(frozen=True)
class OddsColumns:
    """Candidate column names for each price of one source at one file snapshot."""

    source: SourceCode
    closing: bool
    home: tuple[str, ...]
    draw: tuple[str, ...]
    away: tuple[str, ...]
    over_2_5: tuple[str, ...]
    under_2_5: tuple[str, ...]

    def one_x_two_columns(self) -> tuple[str, ...]:
        return self.home + self.draw + self.away


ODDS_COLUMNS: tuple[OddsColumns, ...] = (
    OddsColumns("B365", False, ("B365H",), ("B365D",), ("B365A",), ("B365>2.5",), ("B365<2.5",)),
    OddsColumns(
        "B365", True, ("B365CH",), ("B365CD",), ("B365CA",), ("B365C>2.5",), ("B365C<2.5",)
    ),
    OddsColumns("BW", False, ("BWH",), ("BWD",), ("BWA",), (), ()),
    OddsColumns("BW", True, ("BWCH",), ("BWCD",), ("BWCA",), (), ()),
    OddsColumns(
        "AVG",
        False,
        ("AvgH", "BbAvH"),
        ("AvgD", "BbAvD"),
        ("AvgA", "BbAvA"),
        ("Avg>2.5", "BbAv>2.5"),
        ("Avg<2.5", "BbAv<2.5"),
    ),
    OddsColumns("AVG", True, ("AvgCH",), ("AvgCD",), ("AvgCA",), ("AvgC>2.5",), ("AvgC<2.5",)),
    OddsColumns("PS", False, ("PSH",), ("PSD",), ("PSA",), ("P>2.5",), ("P<2.5",)),
    OddsColumns("PS", True, ("PSCH",), ("PSCD",), ("PSCA",), ("PC>2.5",), ("PC<2.5",)),
)


def snapshot_for(columns: OddsColumns, *, live: bool) -> Snapshot | None:
    """Snapshot a column group maps to, or ``None`` if it isn't used for this kind of file."""
    if live:
        return None if columns.closing else "live"
    return "closing" if columns.closing else "pre_match"
