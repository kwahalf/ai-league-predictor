"""football-data.co.uk CSV parsing: pure functions from file contents to canonical records."""

from .columns import ODDS_COLUMNS, OddsColumns
from .csv_parser import (
    SOURCE_TIMEZONE,
    CsvFormatError,
    UnknownLayoutError,
    parse_fixtures_csv,
    parse_season_csv,
)
from .models import MatchRecord, OddsQuote
from .teams import TEAM_ALIASES, UnknownTeamError, canonical_team

__all__ = [
    "ODDS_COLUMNS",
    "SOURCE_TIMEZONE",
    "TEAM_ALIASES",
    "CsvFormatError",
    "MatchRecord",
    "OddsColumns",
    "OddsQuote",
    "UnknownLayoutError",
    "UnknownTeamError",
    "canonical_team",
    "parse_fixtures_csv",
    "parse_season_csv",
]
