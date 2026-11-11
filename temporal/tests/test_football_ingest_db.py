"""Integration test of the ``football_ingest_matches`` RPC against a real (dev) database.

Opt-in, because it writes: set ``FOOTBALL_INGEST_DB_TESTS=1`` and point ``SUPABASE_URL`` /
``SUPABASE_SERVICE_ROLE_KEY`` at the **dev** stack. It only touches season ``9900`` (1999/00 is
outside the data, and the code wraps to 2099/00 for the kick-offs used here) and its ingest-state
rows, and deletes them before and after. It makes sure Arsenal and Chelsea exist first, so it
works on a freshly reset database as well as after a backfill. CI doesn't run it (no database
there).

    docker compose run --rm -e FOOTBALL_INGEST_DB_TESTS=1 -v "$PWD/temporal:/app" \
      --entrypoint python temporal-worker -m pytest tests/test_football_ingest_db.py -q
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from urllib.request import Request, urlopen

import pytest

from src.config import settings
from src.football.data import MatchRecord, OddsQuote
from src.football.ingest.gateway import HttpGateway
from src.football.ingest.payload import build_payload

pytestmark = pytest.mark.skipif(
    os.environ.get("FOOTBALL_INGEST_DB_TESTS") != "1",
    reason="writes to a database; set FOOTBALL_INGEST_DB_TESTS=1 against the dev stack",
)

SEASON = "9900"
KICKOFF = datetime(2099, 8, 20, 14, 0, tzinfo=timezone.utc)


def _rest(path: str, method: str = "GET"):
    req = Request(
        settings.supabase_url.rstrip("/") + "/rest/v1/" + path,
        method=method,
        headers={
            "apikey": settings.supabase_service_role_key,
            "Authorization": "Bearer " + settings.supabase_service_role_key,
        },
    )
    with urlopen(req, timeout=30) as response:
        body = response.read()
    return json.loads(body) if body else None


def _cleanup() -> None:
    _rest(f"matches?season=eq.{SEASON}", method="DELETE")
    for key in (f"E0/{SEASON}", f"test-fixtures-{SEASON}"):
        _rest(f"football_ingest_state?division=eq.E0&source_key=eq.{key}", method="DELETE")


@pytest.fixture
def gateway():
    _cleanup()
    gw = HttpGateway(settings.supabase_url, settings.supabase_service_role_key)
    # Make sure the two teams exist (with their aliases), so the counts below only see this test.
    teams = build_payload([_fixture(2.0)], include_stats=False).teams
    gw.rpc(
        "football_ingest_matches",
        {"p_division": "E0", "p_kind": "season", "p_source_key": f"E0/{SEASON}",
         "p_captured_at": "2000-01-01T00:00:00+00:00", "p_teams": teams, "p_matches": []},
    )
    yield gw
    _cleanup()


def _ingest(gateway: HttpGateway, kind: str, record: MatchRecord, captured_at: datetime) -> dict:
    payload = build_payload([record], include_stats=kind == "season")
    return gateway.rpc(
        "football_ingest_matches",
        {
            "p_division": "E0",
            "p_kind": kind,
            "p_source_key": f"test-fixtures-{SEASON}" if kind == "fixtures" else f"E0/{SEASON}",
            "p_captured_at": captured_at.isoformat(),
            "p_teams": payload.teams,
            "p_matches": payload.matches,
        },
    )


def _fixture(home_price: float) -> MatchRecord:
    return MatchRecord(
        season=SEASON, match_date=KICKOFF.date(), kickoff_at=KICKOFF,
        home_team="Arsenal", away_team="Chelsea",
        odds=(OddsQuote("B365", "live", home_price, 3.5, 4.0, 1.8, 2.0),),
    )


def _row() -> dict:
    rows = _rest(f"matches?season=eq.{SEASON}&select=id,status,home_goals,source_key,updated_at")
    assert len(rows) == 1
    return rows[0]


def _changes(counts: dict) -> dict:
    return {k: v for k, v in counts.items() if v and k not in ("fixtures_skipped", "stale_skipped")}


def test_fixture_then_result_is_one_row_and_reruns_change_nothing(gateway):
    before_kickoff = datetime(2099, 8, 18, 6, 0, tzinfo=timezone.utc)

    first = _ingest(gateway, "fixtures", _fixture(2.0), before_kickoff)
    assert _changes(first) == {"matches_inserted": 1, "odds_inserted": 1}
    created = _row()
    assert created["status"] == "scheduled"
    live = _rest(f"odds?match_id=eq.{created['id']}&select=snapshot,home,captured_at")
    assert live == [{"snapshot": "live", "home": 2.0, "captured_at": "2099-08-18T06:00:00+00:00"}]

    # Same file again, downloaded later: nothing written, captured_at unchanged.
    again = _ingest(gateway, "fixtures", _fixture(2.0), datetime(2099, 8, 18, 7, 0, tzinfo=timezone.utc))
    assert _changes(again) == {}
    assert _row()["updated_at"] == created["updated_at"]
    assert _rest(f"odds?match_id=eq.{created['id']}&select=captured_at")[0]["captured_at"] == live[0]["captured_at"]

    # The price moved: the live row is updated and re-stamped.
    moved = _ingest(gateway, "fixtures", _fixture(2.1), datetime(2099, 8, 19, 6, 0, tzinfo=timezone.utc))
    assert _changes(moved) == {"odds_updated": 1}

    # The season file now has the result: same match row, now finished, with stats.
    result = MatchRecord(
        season=SEASON, match_date=KICKOFF.date(), kickoff_at=KICKOFF,
        home_team="Arsenal", away_team="Chelsea", home_goals=2, away_goals=1, result="H",
        home_shots=12, away_shots=7,
        odds=(OddsQuote("B365", "pre_match", 2.05, 3.4, 4.1, None, None),),
    )
    finished = _ingest(gateway, "season", result, datetime(2099, 8, 21, 6, 0, tzinfo=timezone.utc))
    assert _changes(finished) == {"matches_updated": 1, "stats_inserted": 1, "odds_inserted": 1}
    row = _row()
    assert row["id"] == created["id"]
    assert (row["status"], row["home_goals"], row["source_key"]) == ("finished", 2, f"E0/{SEASON}")

    # Re-running the season file changes nothing.
    assert _changes(_ingest(gateway, "season", result, datetime(2099, 8, 22, tzinfo=timezone.utc))) == {}
    assert _row()["updated_at"] == row["updated_at"]

    # A stale fixtures.csv listing the match again doesn't turn it back into a fixture or add odds.
    stale = _ingest(gateway, "fixtures", _fixture(9.0), datetime(2099, 8, 22, tzinfo=timezone.utc))
    assert _changes(stale) == {}
    assert stale["fixtures_skipped"] == 1
    assert _row()["status"] == "finished"


def test_live_odds_are_not_captured_after_kickoff(gateway):
    after_kickoff = datetime(2099, 8, 20, 15, 0, tzinfo=timezone.utc)
    counts = _ingest(gateway, "fixtures", _fixture(2.0), after_kickoff)
    assert _changes(counts) == {"matches_inserted": 1}
    assert counts["fixtures_skipped"] == 1
    assert _rest(f"odds?match_id=eq.{_row()['id']}&select=id") == []


def test_a_quote_the_file_no_longer_has_is_deleted(gateway):
    t0 = datetime(2099, 8, 18, 6, 0, tzinfo=timezone.utc)
    assert _changes(_ingest(gateway, "fixtures", _fixture(2.0), t0)) == {"matches_inserted": 1, "odds_inserted": 1}
    # Next download: every Bet365 price is <= 1.0, so nothing is left of the quote after cleaning.
    gone = MatchRecord(
        season=SEASON, match_date=KICKOFF.date(), kickoff_at=KICKOFF, home_team="Arsenal",
        away_team="Chelsea", odds=(OddsQuote("B365", "live", 1.0, 0.0, 0.0, 0.0, 0.0),),
    )
    later = datetime(2099, 8, 19, 6, 0, tzinfo=timezone.utc)
    assert _changes(_ingest(gateway, "fixtures", gone, later)) == {"odds_deleted": 1}
    assert _rest(f"odds?match_id=eq.{_row()['id']}&select=id") == []
    assert _changes(_ingest(gateway, "fixtures", gone, later)) == {}


def test_an_older_download_never_overwrites_a_newer_one(gateway):
    older, newer = (datetime(2099, 8, 18, h, 0, tzinfo=timezone.utc) for h in (6, 7))
    _ingest(gateway, "fixtures", _fixture(2.0), newer)
    # A slower, overlapping run that downloaded earlier loads afterwards: skipped.
    stale = _ingest(gateway, "fixtures", _fixture(9.0), older)
    assert stale == {"stale_skipped": 1}
    assert _rest(f"odds?match_id=eq.{_row()['id']}&select=home") == [{"home": 2.0}]
    # Retrying the same download (same time) is allowed and changes nothing.
    assert _changes(_ingest(gateway, "fixtures", _fixture(2.0), newer)) == {}


def test_unknown_odds_source_fails_loudly(gateway):
    payload = build_payload([_fixture(2.0)], include_stats=False)
    payload.matches[0]["odds"][0]["source"] = "XX"
    with pytest.raises(Exception, match="HTTP 400"):
        gateway.rpc(
            "football_ingest_matches",
            {
                "p_division": "E0", "p_kind": "fixtures", "p_source_key": f"test-fixtures-{SEASON}",
                "p_captured_at": date(2099, 8, 1).isoformat(),
                "p_teams": payload.teams, "p_matches": payload.matches,
            },
        )
