"""The data-quality checks (plan step 1.4) against a real (dev) database, tripped by corrupted files.

Opt-in, because it writes: set ``FOOTBALL_INGEST_DB_TESTS=1`` and point ``SUPABASE_URL`` /
``SUPABASE_SERVICE_ROLE_KEY`` at the **dev** stack. CI doesn't run it (no database there).

Each test loads files from ``fixtures/football/quality/`` through the real load activity
(``load_csv``: parser, odds cleaning, ``football_ingest_matches`` RPC) and then runs the real
``football_quality_check`` RPC and rules. It only writes throwaway seasons and deletes them before
and after:

- ``9900`` (1999/00): before the current season, so it is a **completed** season and must be whole.
  ``E0_9900_complete.csv`` is a full 20-team double round robin (380 matches, 1999-08-07 to
  2000-04-22); the other ``E0_9900_*`` files are copies with one deliberate fault each.
- ``6869`` and ``6970`` (2068/69, 2069/70): after the current season, so the season-shape rules
  don't apply to them and only the targeted check trips.

The checks look at the whole league, so assertions are about the messages naming these seasons;
the real backfilled data next to them is expected to pass (``test_real_data_passes``).

    docker compose run --rm -e FOOTBALL_INGEST_DB_TESTS=1 -v "$PWD/temporal:/app" \
      --entrypoint python temporal-worker -m pytest tests/test_football_quality_db.py -q
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from temporalio.exceptions import ApplicationError

from src.config import settings
from src.football.ingest.activities import load_csv
from src.football.ingest.gateway import HttpGateway
from src.football.ingest.models import RawCsvRef, season_for
from src.football.quality.activities import RPC_ROW_LIMIT, QUALITY_RPC, check_quality, expected_completed_seasons
from src.football.quality.checks import MARGIN_LIMITS, TEAMS_PER_SEASON, evaluate
from src.football.quality.models import QualityRequest

pytestmark = pytest.mark.skipif(
    os.environ.get("FOOTBALL_INGEST_DB_TESTS") != "1",
    reason="writes to a database; set FOOTBALL_INGEST_DB_TESTS=1 against the dev stack",
)

FIXTURES = Path(__file__).parent / "fixtures" / "football" / "quality"
TEST_SEASONS = ("9900", "6869", "6970")
CURRENT = season_for(date.today())


def _rest(path: str, method: str = "GET", body: dict | None = None):
    req = Request(
        settings.supabase_url.rstrip("/") + "/rest/v1/" + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "apikey": settings.supabase_service_role_key,
            "Authorization": "Bearer " + settings.supabase_service_role_key,
            "Content-Type": "application/json",
        },
    )
    with urlopen(req, timeout=30) as response:
        raw = response.read()
    return json.loads(raw) if raw else None


def _cleanup() -> None:
    for season in TEST_SEASONS:
        _rest(f"matches?season=eq.{season}", method="DELETE")
        _rest(f"football_ingest_state?division=eq.E0&source_key=eq.E0/{season}", method="DELETE")


class LocalFileGateway(HttpGateway):
    """The real gateway, except that the "stored raw file" is a local fixture."""

    def __init__(self) -> None:
        super().__init__(settings.supabase_url, settings.supabase_service_role_key)
        self.files: dict[str, bytes] = {}

    def get_object(self, path: str) -> bytes:
        return self.files[path]


@pytest.fixture
def gateway():
    _cleanup()
    yield LocalFileGateway()
    _cleanup()


def _load(gateway: LocalFileGateway, name: str, season: str):
    data = (FIXTURES / name).read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    path = f"test-quality/{season}/{sha}.csv"
    gateway.files[path] = data
    ref = RawCsvRef(
        kind="season", season=season, division="E0", url=f"fixture:{name}", storage_path=path, sha256=sha,
        size_bytes=len(data), downloaded_at=datetime.now(timezone.utc).isoformat(),
    )
    return load_csv(gateway, ref)


def _report(gateway: HttpGateway):
    facts = gateway.rpc(
        QUALITY_RPC,
        {
            "p_division": "E0",
            "p_current_season": CURRENT,
            "p_expected_seasons": expected_completed_seasons(CURRENT),
            "p_teams_per_season": TEAMS_PER_SEASON["E0"],
            "p_margin_limits": {m: {"min": lo, "max": hi} for m, (lo, hi) in MARGIN_LIMITS.items()},
            "p_limit": RPC_ROW_LIMIT,
        },
    )
    return evaluate(facts, division="E0", current_season=CURRENT)


def _message(report, name: str) -> str:
    return next(c for c in report.checks if c.name == name).message


def _mentions_test_seasons(report) -> list[str]:
    return [
        c.name for c in report.checks
        if any(f"season {s}" in c.message or f"{s} " in c.message for s in TEST_SEASONS)
    ]


def test_real_data_passes(gateway):
    """The backfilled data passes every check (run after a backfill, as on the dev stack)."""
    report = check_quality(gateway, QualityRequest(current_season=CURRENT))
    assert report.failed == []
    assert report.matches_checked >= 3800


def test_a_whole_completed_season_passes(gateway):
    result = _load(gateway, "E0_9900_complete.csv", "9900")
    assert result.matches_inserted == 380
    assert _mentions_test_seasons(_report(gateway)) == []


def test_a_missing_match_trips_the_completed_season_check(gateway):
    _load(gateway, "E0_9900_missing_match.csv", "9900")
    message = _message(_report(gateway), "completed_seasons")
    assert "season 9900: 379 matches, expected 380" in message
    assert "2 teams without 19 home and 19 away matches" in message
    assert "1 home/away pairs with no match" in message


def test_an_unplayed_match_trips_the_completed_season_check(gateway):
    _load(gateway, "E0_9900_unplayed_match.csv", "9900")
    message = _message(_report(gateway), "completed_seasons")
    assert "season 9900: 1 matches not finished:" in message
    assert "(scheduled)" in message


def test_a_wrong_team_trips_the_completed_season_check(gateway):
    _load(gateway, "E0_9900_wrong_team.csv", "9900")
    message = _message(_report(gateway), "completed_seasons")
    assert "season 9900: 21 teams, expected 20" in message
    assert "Fulham 1 (1 home, 0 away)" in message


def test_a_match_stored_under_two_seasons_trips_the_duplicate_check(gateway):
    _load(gateway, "E0_6970_valid.csv", "6970")
    assert _mentions_test_seasons(_report(gateway)) == []
    _load(gateway, "E0_6869_stray_match.csv", "6869")
    message = _message(_report(gateway), "duplicate_fixtures")
    assert "2069-08-20: 6869 Arsenal v Chelsea and 6970 Arsenal v Chelsea share a team on the same day" in message


def test_a_team_twice_on_one_day_trips_the_duplicate_check(gateway):
    _load(gateway, "E0_6970_team_twice_same_day.csv", "6970")
    message = _message(_report(gateway), "duplicate_fixtures")
    assert "2069-08-20: 6970 Arsenal v Chelsea and 6970 Liverpool v Arsenal share a team on the same day" in message


def test_margins_outside_the_limits_trip_the_margin_check(gateway):
    _load(gateway, "E0_6970_bad_margins.csv", "6970")
    message = _message(_report(gateway), "odds_margins")
    assert "6970 2069-08-20 Arsenal v Chelsea B365 pre_match 1X2 1.5/3/4: margin 25.00%" in message
    assert "6970 2069-08-27 Chelsea v Arsenal B365 pre_match 1X2 3/4/5: margin -21.67%" in message
    assert "6970 2069-09-03 Liverpool v Everton B365 pre_match over/under 2.5 1.5/1.5: margin 33.33%" in message
    assert "6970 2069-09-10 Everton v Liverpool B365 pre_match over/under 2.5 2.5/2.5: margin -20.00%" in message


def test_a_price_of_one_never_reaches_the_database(gateway):
    # The ingest stores it as missing (1.3), so the price check has nothing to find.
    result = _load(gateway, "E0_6970_price_at_one.csv", "6970")
    assert (result.prices_dropped, result.price_sets_nulled) == (1, 1)
    assert _mentions_test_seasons(_report(gateway)) == []
    # And the schema refuses one written directly; the check is the backstop if that ever changes.
    (match,) = _rest("matches?season=eq.6970&select=id")
    (quote,) = _rest(f"odds?match_id=eq.{match['id']}&select=id,home,over_2_5")
    assert quote["home"] is None and quote["over_2_5"] == 1.9
    with pytest.raises(HTTPError) as info:
        _rest(f"odds?id=eq.{quote['id']}", method="PATCH", body={"over_2_5": 1.0})
    assert info.value.code == 400


def test_a_score_on_an_unfinished_match_trips_the_score_check(gateway):
    # A file can't do it: a lone score is refused by the schema and nothing is written.
    with pytest.raises(ApplicationError):
        _load(gateway, "E0_6970_one_score.csv", "6970")
    assert _rest("matches?season=eq.6970&select=id") == []

    # Corrupt the status directly: a scored match marked postponed.
    _load(gateway, "E0_6970_valid.csv", "6970")
    _rest("matches?season=eq.6970", method="PATCH", body={"status": "postponed"})
    message = _message(_report(gateway), "scores_match_status")
    assert "6970 2069-08-20 Arsenal v Chelsea: status postponed, score 2-1" in message

    # A finished match without scores is refused by the schema.
    with pytest.raises(HTTPError) as info:
        _rest("matches?season=eq.6970", method="PATCH",
              body={"status": "finished", "home_goals": None, "away_goals": None})
    assert info.value.code == 400


def test_a_failed_check_raises_the_data_quality_error(gateway):
    _load(gateway, "E0_9900_missing_match.csv", "9900")
    with pytest.raises(ApplicationError) as info:
        check_quality(gateway, QualityRequest(current_season=CURRENT))
    assert info.value.type == "DataQualityError" and info.value.non_retryable
    assert "season 9900: 379 matches, expected 380" in info.value.message
