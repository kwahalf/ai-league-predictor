"""Integration tests of the model tables and market scoring against a real (dev) database.

Opt-in, because it writes: set ``FOOTBALL_INGEST_DB_TESTS=1`` and point ``SUPABASE_URL`` /
``SUPABASE_SERVICE_ROLE_KEY`` at the **dev** stack. It only touches season ``9899`` (1998/99,
outside the data) and model versions named ``test-2.1b-db*``, and deletes them before and after.
No prediction is ever stored (predictions are append-only, so one could not be removed again): every insert here
is meant to fail. CI doesn't run it (no database there).

    docker compose run --rm -e FOOTBALL_INGEST_DB_TESTS=1 -v "$PWD/temporal:/app" \
      --entrypoint python temporal-worker -m pytest tests/test_football_scoring_db.py -q
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from src.config import settings
from src.football.data import MatchRecord, OddsQuote
from src.football.ingest.gateway import HttpGateway
from src.football.ingest.payload import build_payload
from src.football.scoring.activities import score_market
from src.football.scoring.models import SeasonRef

pytestmark = pytest.mark.skipif(
    os.environ.get("FOOTBALL_INGEST_DB_TESTS") != "1",
    reason="writes to a database; set FOOTBALL_INGEST_DB_TESTS=1 against the dev stack",
)

SEASON = "9899"
MODEL = "test-2.1b-db"
PAST = datetime(1998, 8, 20, 14, 0, tzinfo=timezone.utc)
FUTURE = datetime(2098, 8, 20, 14, 0, tzinfo=timezone.utc)


def _rest(path: str, method: str = "GET", body=None, prefer: str | None = None):
    headers = {
        "apikey": settings.supabase_service_role_key,
        "Authorization": "Bearer " + settings.supabase_service_role_key,
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    req = Request(
        settings.supabase_url.rstrip("/") + "/rest/v1/" + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers=headers,
    )
    with urlopen(req, timeout=30) as response:
        data = response.read()
    return json.loads(data) if data else None


def _rest_error(path: str, method: str, body=None) -> dict:
    """Make a request that must fail; return PostgREST's error body."""
    with pytest.raises(HTTPError) as info:
        _rest(path, method, body)
    return json.loads(info.value.read() or b"{}")


def _cleanup() -> None:
    _rest(f"matches?season=eq.{SEASON}", method="DELETE")
    _rest(f"football_ingest_state?division=eq.E0&source_key=eq.E0/{SEASON}", method="DELETE")
    _rest(f"model_versions?name=like.{MODEL}*", method="DELETE")


def _load(gateway: HttpGateway, records: list[MatchRecord]) -> None:
    payload = build_payload(records, include_stats=False)
    gateway.rpc(
        "football_ingest_matches",
        {"p_division": "E0", "p_kind": "season", "p_source_key": f"E0/{SEASON}",
         "p_captured_at": datetime.now(timezone.utc).isoformat(),
         "p_teams": payload.teams, "p_matches": payload.matches},
    )


def _match_id(home: str, away: str) -> str:
    teams = {t["name"]: t["id"] for t in _rest("teams?select=id,name&name=in.(Arsenal,Chelsea,Everton)")}
    rows = _rest(f"matches?season=eq.{SEASON}&home_team_id=eq.{teams[home]}&away_team_id=eq.{teams[away]}&select=id")
    return rows[0]["id"]


@pytest.fixture
def setup():
    _cleanup()
    gateway = HttpGateway(settings.supabase_url, settings.supabase_service_role_key)
    odds = (
        OddsQuote("B365", "pre_match", 2.10, 3.40, 3.60, None, None),
        OddsQuote("BW", "pre_match", 2.05, 3.50, 3.70, None, None),
        OddsQuote("PS", "closing", 1.98, 3.70, 4.20, None, None),
    )
    _load(gateway, [
        # Kicked off long ago but still scheduled (no result): only the clock rejects a prediction.
        MatchRecord(season=SEASON, match_date=PAST.date(), kickoff_at=PAST,
                    home_team="Arsenal", away_team="Chelsea"),
        # Far in the future: a prediction is on time.
        MatchRecord(season=SEASON, match_date=FUTURE.date(), kickoff_at=FUTURE,
                    home_team="Chelsea", away_team="Arsenal"),
        # Finished, with odds: for market scoring.
        MatchRecord(season=SEASON, match_date=PAST.date(), kickoff_at=PAST, home_team="Arsenal",
                    away_team="Everton", home_goals=2, away_goals=1, result="H", odds=odds),
    ])
    model = _rest("model_versions", "POST", {"name": MODEL}, prefer="return=representation")[0]
    yield gateway, model["id"]
    _cleanup()


def _prediction(match_id: str, model_id: str, **overrides) -> dict:
    row = {
        "match_id": match_id, "model_version_id": model_id,
        "p_home": 0.5, "p_draw": 0.3, "p_away": 0.2,
        "f_home": 0.5, "f_draw": 0.3, "f_away": 0.2,
        "e_home": 0.5, "e_draw": 0.3, "e_away": 0.2,
        "xg_home": 1.5, "xg_away": 1.0, "p_over_2_5": 0.5, "p_btts": 0.5, "used_odds": False,
    }
    row.update(overrides)
    return row


def test_deadline_is_kickoff_or_the_start_of_the_uk_date():
    def deadline(kickoff: str) -> str:
        return _rest("rpc/football_prediction_deadline", "POST", {"p_kickoff_at": kickoff})

    assert deadline("2019-08-10T14:00:00+00:00") == "2019-08-10T14:00:00+00:00"
    # Date-only kick-offs (stored as 00:00 UTC): midnight UK time, i.e. 23:00 UTC in summer.
    assert deadline("2018-08-11T00:00:00+00:00") == "2018-08-10T23:00:00+00:00"
    assert deadline("2018-12-15T00:00:00+00:00") == "2018-12-15T00:00:00+00:00"


def test_a_late_prediction_is_rejected(setup):
    _, model_id = setup
    error = _rest_error("predictions", "POST", _prediction(_match_id("Arsenal", "Chelsea"), model_id))
    assert "too late" in error["message"]


def test_a_backdated_created_at_does_not_help(setup):
    _, model_id = setup
    late = _prediction(_match_id("Arsenal", "Chelsea"), model_id, created_at="1998-01-01T00:00:00+00:00")
    assert "too late" in _rest_error("predictions", "POST", late)["message"]


def test_a_prediction_before_kickoff_passes_the_trigger(setup):
    _, model_id = setup
    on_time = _prediction(_match_id("Chelsea", "Arsenal"), model_id)
    bad_sum = _prediction(_match_id("Chelsea", "Arsenal"), model_id, p_home=0.9)
    # One statement: the on-time row passes the trigger, the second fails its check constraint,
    # and the whole insert rolls back, so nothing is stored.
    error = _rest_error("predictions", "POST", [on_time, bad_sum])
    assert error["code"] == "23514" and "violates check constraint" in error["message"]
    assert "too late" not in error["message"]
    assert _rest(f"predictions?model_version_id=eq.{model_id}&select=id") == []


def test_predictions_cannot_be_updated_or_deleted():
    any_id = uuid.uuid4()
    assert "append-only" in _rest_error(f"predictions?id=eq.{any_id}", "PATCH", {"p_home": 0.4})["message"]
    assert "append-only" in _rest_error(f"predictions?id=eq.{any_id}", "DELETE")["message"]


def test_only_one_model_version_is_current(setup):
    if _rest("model_versions?is_current=is.true&select=id"):
        pytest.skip("the database already has a current model version")
    _rest("model_versions", "POST", {"name": MODEL + "-a", "is_current": True})
    error = _rest_error("model_versions", "POST", {"name": MODEL + "-b", "is_current": True})
    assert error["code"] == "23505" and "model_versions_one_current" in error["message"]


def test_market_scores_are_written_once_and_a_rerun_changes_nothing(setup):
    gateway, _ = setup
    first = score_market(gateway, SeasonRef(SEASON))
    assert (first.matches, first.matches_scored) == (3, 1)
    # B365 and BW pre-match, Pinnacle closing, and the pre-match consensus (no closing consensus:
    # no model-input source has closing odds here).
    assert (first.inserted, first.updated, first.deleted) == (4, 0, 0)
    match_id = _match_id("Arsenal", "Everton")
    rows = _rest(f"market_scores?match_id=eq.{match_id}&select=bookmaker_id,snapshot,rps,updated_at,scored_at")
    assert len(rows) == 4
    assert sum(r["bookmaker_id"] is None for r in rows) == 1

    again = score_market(gateway, SeasonRef(SEASON))
    assert (again.inserted, again.updated, again.deleted) == (0, 0, 0)
    after = _rest(f"market_scores?match_id=eq.{match_id}&select=bookmaker_id,snapshot,rps,updated_at,scored_at")
    key = lambda r: (r["bookmaker_id"] or "", r["snapshot"])  # noqa: E731
    assert sorted(after, key=key) == sorted(rows, key=key)

    # The consensus row can't be duplicated (NULLS NOT DISTINCT).
    consensus = {"match_id": match_id, "bookmaker_id": None, "snapshot": "pre_match", "p_home": 0.5,
                 "p_draw": 0.3, "p_away": 0.2, "rps": 0.1, "log_loss": 0.1, "brier": 0.1}
    assert _rest_error("market_scores", "POST", consensus)["code"] == "23505"


def test_a_batch_from_changed_inputs_is_refused(setup):
    gateway, _ = setup
    inputs = gateway.rpc("football_market_score_inputs", {"p_division": "E0", "p_season": SEASON})
    assert len(inputs["matches"]) == 3 and inputs["revision"]
    counts = gateway.rpc(
        "football_upsert_market_scores",
        {"p_division": "E0", "p_season": SEASON, "p_revision": "not-the-revision", "p_scores": []},
    )
    assert counts == {"inserted": 0, "updated": 0, "deleted": 0, "stale": True}
