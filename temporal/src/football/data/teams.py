"""Team-name normaliser: every spelling seen in the source files maps to one canonical club name.

The alias lists cover every name in the E0 season files 2016/17 to 2026/27 and the E0 rows of
``fixtures.csv`` (checked 2026-11-11; the committed list is
``temporal/tests/fixtures/football/team_names.json``). A name that isn't listed raises
``UnknownTeamError`` instead of silently creating a new club: add the new spelling here.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping


class UnknownTeamError(ValueError):
    """Raised when a team name has no entry in the alias table."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"Unknown team name {name!r}: add it to TEAM_ALIASES in "
            "temporal/src/football/data/teams.py"
        )
        self.name = name


# Canonical name -> spellings used by football-data.co.uk. The canonical name itself always resolves.
TEAM_ALIASES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "Arsenal": ("Arsenal",),
        "Aston Villa": ("Aston Villa",),
        "Bournemouth": ("Bournemouth",),
        "Brentford": ("Brentford",),
        "Brighton & Hove Albion": ("Brighton",),
        "Burnley": ("Burnley",),
        "Cardiff City": ("Cardiff",),
        "Chelsea": ("Chelsea",),
        "Coventry City": ("Coventry",),
        "Crystal Palace": ("Crystal Palace",),
        "Everton": ("Everton",),
        "Fulham": ("Fulham",),
        "Huddersfield Town": ("Huddersfield",),
        "Hull City": ("Hull",),
        "Ipswich Town": ("Ipswich",),
        "Leeds United": ("Leeds",),
        "Leicester City": ("Leicester",),
        "Liverpool": ("Liverpool",),
        "Luton Town": ("Luton",),
        "Manchester City": ("Man City",),
        "Manchester United": ("Man United",),
        "Middlesbrough": ("Middlesbrough",),
        "Newcastle United": ("Newcastle",),
        "Norwich City": ("Norwich",),
        "Nottingham Forest": ("Nott'm Forest",),
        "Sheffield United": ("Sheffield United",),
        "Southampton": ("Southampton",),
        "Stoke City": ("Stoke",),
        "Sunderland": ("Sunderland",),
        "Swansea City": ("Swansea",),
        "Tottenham Hotspur": ("Tottenham",),
        "Watford": ("Watford",),
        "West Bromwich Albion": ("West Brom",),
        "West Ham United": ("West Ham",),
        "Wolverhampton Wanderers": ("Wolves",),
    }
)


def _key(name: str) -> str:
    return " ".join(name.split()).casefold()


def _build_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for canonical, aliases in TEAM_ALIASES.items():
        for spelling in (canonical, *aliases):
            key = _key(spelling)
            existing = lookup.setdefault(key, canonical)
            if existing != canonical:
                raise ValueError(f"Alias {spelling!r} maps to both {existing!r} and {canonical!r}")
    return lookup


_LOOKUP = _build_lookup()


def canonical_team(name: str) -> str:
    """Return the canonical club name for ``name``.

    Matching ignores case and repeated or surrounding whitespace. Raises ``UnknownTeamError``
    for a name not in ``TEAM_ALIASES``.
    """
    canonical = _LOOKUP.get(_key(name))
    if canonical is None:
        raise UnknownTeamError(name)
    return canonical
