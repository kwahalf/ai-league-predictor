"""Tests for the ingest (plan step 1.3): seasons, payload cleaning, activities and the schedule.

No network and no database: activities run against ``FakeGateway``. The workflow's orchestration
is in ``test_football_ingest_workflow.py``; the SQL upsert is exercised against a real database by
the opt-in ``test_football_ingest_db.py``.
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError

import pytest
from temporalio.client import ScheduleAlreadyRunningError, ScheduleOverlapPolicy, ScheduleRange
from temporalio.exceptions import ApplicationError

from src.football.data import MatchRecord, OddsQuote
from src.football.ingest import cli
from src.football.ingest.activities import build_activities, fetch_csv, load_csv, storage_path
from src.football.ingest.gateway import _is_duplicate, fixtures_url, season_url
from src.football.ingest.models import (
    CsvSource,
    IngestRequest,
    LoadResult,
    RawCsvRef,
    season_for,
    season_range,
    validate_season,
)
from src.football.ingest.payload import DuplicateMatchError, build_payload, clean_quote, kickoff_iso

FIXTURES = Path(__file__).parent / "fixtures" / "football"
NOW = datetime(2026, 11, 11, 6, 0, 5, tzinfo=timezone.utc)


class FakeGateway:
    """In-memory stand-in for football-data.co.uk, Storage and the RPC."""

    def __init__(self, files: dict[str, bytes] | None = None, rpc_result: dict | None = None) -> None:
        self.files = files or {}
        self.objects: dict[str, bytes] = {}
        self.rpc_calls: list[tuple[str, dict]] = []
        self.rpc_result = rpc_result if rpc_result is not None else {}

    def download(self, url: str) -> bytes:
        return self.files[url]

    def put_object(self, path: str, data: bytes) -> bool:
        if path in self.objects:
            return False
        self.objects[path] = data
        return True

    def get_object(self, path: str) -> bytes:
        return self.objects[path]

    def rpc(self, function: str, args: dict):
        # The real gateway sends JSON: make sure the payload survives it.
        self.rpc_calls.append((function, json.loads(json.dumps(args))))
        return self.rpc_result


def _quote(**prices) -> OddsQuote:
    base = dict(home=None, draw=None, away=None, over_2_5=None, under_2_5=None)
    base.update(prices)
    return OddsQuote("B365", "closing", **base)


def _record(home="Arsenal", away="Chelsea", **kwargs) -> MatchRecord:
    base = dict(season="2122", match_date=date(2021, 12, 19), kickoff_at=None, home_team=home, away_team=away)
    base.update(kwargs)
    return MatchRecord(**base)


# ── Seasons ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "day, season",
    [
        (date(2026, 11, 11), "2627"),
        (date(2027, 5, 30), "2627"),
        (date(2027, 6, 30), "2627"),
        (date(2027, 7, 1), "2728"),
        (date(2099, 8, 1), "9900"),
    ],
)
def test_season_for(day, season):
    assert season_for(day) == season


def test_season_range_covers_the_backfill():
    seasons = season_range("1617", "2627")
    assert seasons[0] == "1617" and seasons[-1] == "2627"
    assert len(seasons) == 11
    assert season_range("9899", "0001") == ["9899", "9900", "0001"]


@pytest.mark.parametrize("bad", ["2016", "16/17", "1618", "", "abcd", None])
def test_validate_season_rejects_bad_codes(bad):
    with pytest.raises(ValueError):
        validate_season(bad)


def test_season_range_rejects_backwards():
    with pytest.raises(ValueError):
        season_range("2627", "1617")


# ── Odds cleaning (owner decision 2026-11-11) ────────────────────────────


def test_prices_at_or_below_one_are_dropped_and_counted():
    # The real 2021/22 case: Bet365 closing over/under of 0 for Newcastle v Man City.
    cleaned = clean_quote(_quote(home=21.0, draw=9.0, away=1.11, over_2_5=0.0, under_2_5=0.0))
    assert cleaned.prices == dict(home=21.0, draw=9.0, away=1.11, over_2_5=None, under_2_5=None)
    assert cleaned.prices_dropped == 2
    assert cleaned.sets_nulled == 0


def test_exactly_one_is_dropped_but_just_above_is_kept():
    cleaned = clean_quote(_quote(home=1.0, draw=1.001, away=5.0))
    assert cleaned.prices_dropped == 1
    # Dropping home left the 1X2 set incomplete, so the whole set goes.
    assert cleaned.prices == dict(home=None, draw=None, away=None, over_2_5=None, under_2_5=None)
    assert cleaned.sets_nulled == 1
    assert cleaned.is_empty


def test_incomplete_set_from_the_source_is_nulled_without_touching_the_other_set():
    cleaned = clean_quote(_quote(home=2.0, draw=3.0, away=None, over_2_5=1.9, under_2_5=1.95))
    assert cleaned.prices_dropped == 0
    assert cleaned.sets_nulled == 1
    assert cleaned.prices == dict(home=None, draw=None, away=None, over_2_5=1.9, under_2_5=1.95)


def test_one_dropped_over_under_price_nulls_the_pair():
    cleaned = clean_quote(_quote(home=2.0, draw=3.0, away=4.0, over_2_5=0.5, under_2_5=1.8))
    assert cleaned.prices_dropped == 1
    assert cleaned.sets_nulled == 1
    assert cleaned.prices["over_2_5"] is None and cleaned.prices["under_2_5"] is None
    assert cleaned.prices["home"] == 2.0


# ── Payload ──────────────────────────────────────────────────────────────


def test_payload_drops_empty_quotes_and_counts_across_matches():
    records = [
        _record(odds=(_quote(home=0.0, draw=0.0, away=0.0),)),
        _record("Everton", "Fulham", odds=(_quote(home=2.0, draw=3.0, away=4.0, over_2_5=1.0, under_2_5=2.0),)),
    ]
    payload = build_payload(records, include_stats=True)
    assert payload.prices_dropped == 4
    assert payload.price_sets_nulled == 1
    assert payload.matches[0]["odds"] == []
    assert payload.matches[1]["odds"] == [
        {"source": "B365", "snapshot": "closing", "home": 2.0, "draw": 3.0, "away": 4.0,
         "over_2_5": None, "under_2_5": None}
    ]


def test_payload_teams_carry_their_aliases():
    payload = build_payload([_record("Manchester United", "Manchester City")], include_stats=True)
    assert payload.teams == [
        {"name": "Manchester City", "aliases": ["Man City"]},
        {"name": "Manchester United", "aliases": ["Man United"]},
    ]


def test_payload_status_and_stats():
    finished = _record(home_goals=2, away_goals=1, result="H", home_shots=10)
    scheduled = _record("Everton", "Fulham")
    payload = build_payload([finished, scheduled], include_stats=True)
    assert payload.matches[0]["status"] == "finished"
    assert payload.matches[0]["stats"]["home_shots"] == 10
    assert payload.matches[0]["stats"]["away_shots"] is None
    assert payload.matches[1]["status"] == "scheduled"
    assert payload.matches[1]["stats"] is None
    assert build_payload([finished], include_stats=False).matches[0]["stats"] is None


def test_payload_rejects_a_fixture_listed_twice():
    with pytest.raises(DuplicateMatchError):
        build_payload([_record(), _record()], include_stats=True)


def test_kickoff_falls_back_to_midnight_utc_on_the_match_date():
    assert kickoff_iso(_record()) == "2021-12-19T00:00:00+00:00"
    timed = _record(kickoff_at=datetime(2021, 12, 19, 14, 0, tzinfo=timezone.utc))
    assert kickoff_iso(timed) == "2021-12-19T14:00:00+00:00"


# ── fetch activity ───────────────────────────────────────────────────────


def test_fetch_stores_raw_bytes_under_their_hash_once():
    data = (FIXTURES / "E0_2324_sample.csv").read_bytes()
    gateway = FakeGateway({season_url("E0", "2324"): data})
    source = CsvSource("season", "2324")

    first = fetch_csv(gateway, source, now=lambda: NOW)
    second = fetch_csv(gateway, source, now=lambda: NOW + timedelta(days=1))

    digest = hashlib.sha256(data).hexdigest()
    assert first.storage_path == f"E0/2324/{digest}.csv" == second.storage_path
    assert first.sha256 == digest
    assert list(gateway.objects) == [first.storage_path]  # identical content: no second copy
    assert first.downloaded_at == "2026-11-11T06:00:05+00:00"
    assert first.url == "https://www.football-data.co.uk/mmz4281/2324/E0.csv"


def test_fetch_fixtures_path_and_url():
    data = (FIXTURES / "fixtures_sample.csv").read_bytes()
    gateway = FakeGateway({fixtures_url(): data})
    ref = fetch_csv(gateway, CsvSource("fixtures", "2627"), now=lambda: NOW)
    assert ref.storage_path == storage_path(CsvSource("fixtures", "2627"), ref.sha256)
    assert ref.storage_path.startswith("fixtures/")
    assert ref.url == "https://www.football-data.co.uk/fixtures.csv"


def test_fetch_rejects_an_empty_file():
    gateway = FakeGateway({season_url("E0", "2324"): b"  \r\n"})
    with pytest.raises(ApplicationError, match="Empty"):
        fetch_csv(gateway, CsvSource("season", "2324"), now=lambda: NOW)


@pytest.mark.parametrize("source", [CsvSource("results", "2324"), CsvSource("season", "2016"), CsvSource("season", "2324", "E0/../x")])
def test_fetch_rejects_bad_sources_without_retrying(source):
    with pytest.raises(ApplicationError) as info:
        fetch_csv(FakeGateway(), source, now=lambda: NOW)
    assert info.value.non_retryable


# ── load activity ────────────────────────────────────────────────────────


def _fetched(gateway: FakeGateway, name: str, source: CsvSource) -> RawCsvRef:
    url = season_url(source.division, source.season) if source.kind == "season" else fixtures_url()
    gateway.files[url] = (FIXTURES / name).read_bytes()
    return fetch_csv(gateway, source, now=lambda: NOW)


def test_load_season_sends_one_rpc_with_matches_stats_and_odds():
    gateway = FakeGateway(rpc_result={"matches_inserted": 3, "odds_inserted": 24, "teams_inserted": 6})
    ref = _fetched(gateway, "E0_2324_sample.csv", CsvSource("season", "2324"))

    result = load_csv(gateway, ref)

    assert len(gateway.rpc_calls) == 1
    function, args = gateway.rpc_calls[0]
    assert function == "football_ingest_matches"
    assert args["p_division"] == "E0"
    assert args["p_kind"] == "season"
    assert args["p_source_key"] == "E0/2324"
    assert args["p_captured_at"] == "2026-11-11T06:00:05+00:00"
    assert len(args["p_matches"]) == 3
    first = args["p_matches"][0]
    assert first["status"] == "finished"
    assert first["stats"] is not None
    assert {(o["source"], o["snapshot"]) for o in first["odds"]} >= {("B365", "pre_match"), ("B365", "closing")}
    assert {t["name"] for t in args["p_teams"]} >= {first["home_team"], first["away_team"]}

    assert result == LoadResult(
        kind="season", season="2324", storage_path=ref.storage_path, matches_in_file=3,
        matches_inserted=3, odds_inserted=24, teams_inserted=6,
    )


def test_load_fixtures_sends_live_odds_without_stats():
    gateway = FakeGateway(rpc_result={"fixtures_skipped": 1})
    ref = _fetched(gateway, "fixtures_sample.csv", CsvSource("fixtures", "2627"))

    result = load_csv(gateway, ref)

    _, args = gateway.rpc_calls[0]
    assert args["p_kind"] == "fixtures"
    assert args["p_source_key"] == "fixtures.csv"
    assert len(args["p_matches"]) == 2  # only the E0 rows
    for match in args["p_matches"]:
        assert match["status"] == "scheduled"
        assert match["stats"] is None
        assert match["odds"] and {o["snapshot"] for o in match["odds"]} == {"live"}
    assert result.fixtures_skipped == 1


def test_load_counts_dropped_prices():
    csv = (
        "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,B365H,B365D,B365A,B365C>2.5,B365C<2.5\n"
        "E0,19/12/2021,14:00,Newcastle,Man City,0,4,A,13,7.5,1.18,0,0\n"
    ).encode()
    gateway = FakeGateway({season_url("E0", "2122"): csv})
    ref = fetch_csv(gateway, CsvSource("season", "2122"), now=lambda: NOW)

    result = load_csv(gateway, ref)

    assert result.prices_dropped == 2
    odds = gateway.rpc_calls[0][1]["p_matches"][0]["odds"]
    assert odds == [
        {"source": "B365", "snapshot": "pre_match", "home": 13.0, "draw": 7.5, "away": 1.18,
         "over_2_5": None, "under_2_5": None}
    ]  # the closing quote had nothing left, so it isn't sent


def test_load_fails_without_retry_on_bad_data():
    csv = b"Div,Date,HomeTeam,AwayTeam,B365H,B365D,B365A\nE0,01/01/2024,Nowhere Rovers,Arsenal,2,3,4\n"
    gateway = FakeGateway({season_url("E0", "2324"): csv})
    ref = fetch_csv(gateway, CsvSource("season", "2324"), now=lambda: NOW)
    with pytest.raises(ApplicationError) as info:
        load_csv(gateway, ref)
    assert info.value.non_retryable
    assert info.value.type == "UnknownTeamError"
    assert gateway.rpc_calls == []


def test_load_refuses_a_stored_file_that_does_not_match_its_hash():
    gateway = FakeGateway()
    ref = _fetched(gateway, "E0_2324_sample.csv", CsvSource("season", "2324"))
    gateway.objects[ref.storage_path] = b"tampered"
    with pytest.raises(ApplicationError, match="hash"):
        load_csv(gateway, ref)
    assert gateway.rpc_calls == []


def test_build_activities_registers_both_names():
    names = [fn.__temporal_activity_definition.name for fn in build_activities(FakeGateway())]
    assert names == ["football_fetch_csv", "football_load_csv"]


# ── Gateway helpers ──────────────────────────────────────────────────────


def _http_error(code: int, body: bytes = b"") -> HTTPError:
    return HTTPError("http://x", code, "err", {}, io.BytesIO(body))


def test_storage_duplicate_detection():
    assert _is_duplicate(_http_error(409))
    assert _is_duplicate(_http_error(400, b'{"statusCode":"409","error":"Duplicate","message":"exists"}'))
    assert not _is_duplicate(_http_error(400, b'{"statusCode":"400","error":"InvalidKey"}'))
    assert not _is_duplicate(_http_error(500))


# ── Schedule ─────────────────────────────────────────────────────────────


def test_daily_schedule_is_0600_utc_with_no_overlap():
    schedule = cli.daily_schedule("main")
    calendar = schedule.spec.calendars[0]
    assert list(calendar.hour) == [ScheduleRange(6)]
    assert list(calendar.minute) == [ScheduleRange(0)]
    assert list(calendar.second) == [ScheduleRange(0)]
    assert schedule.spec.time_zone_name == "UTC"
    assert schedule.policy.overlap == ScheduleOverlapPolicy.SKIP
    action = schedule.action
    assert action.workflow == "IngestWorkflow"
    assert action.args == [IngestRequest()]  # current season + fixtures.csv
    assert action.task_queue == "main"


class _FakeScheduleHandle:
    def __init__(self, sink: list) -> None:
        self.sink = sink

    async def update(self, updater):
        self.sink.append(("update", updater(None).schedule))


class _FakeScheduleClient:
    def __init__(self) -> None:
        self.calls: list = []
        self.existing: set[str] = set()

    async def create_schedule(self, schedule_id, schedule):
        if schedule_id in self.existing:
            raise ScheduleAlreadyRunningError()
        self.existing.add(schedule_id)
        self.calls.append(("create", schedule_id))

    def get_schedule_handle(self, schedule_id):
        self.calls.append(("handle", schedule_id))
        return _FakeScheduleHandle(self.calls)


async def test_ensure_schedule_creates_once_then_updates_in_place():
    client = _FakeScheduleClient()
    assert await cli.ensure_schedule(client, "main") == "created"
    assert await cli.ensure_schedule(client, "main") == "updated"
    assert client.existing == {"football-ingest-daily"}
    assert [c[0] for c in client.calls] == ["create", "handle", "update"]


def test_load_reports_a_stale_download_skipped_by_the_database():
    gateway = FakeGateway(rpc_result={"stale_skipped": 1})
    ref = _fetched(gateway, "E0_2324_sample.csv", CsvSource("season", "2324"))
    result = load_csv(gateway, ref)
    assert result.stale_skipped == 1
    assert result.matches_inserted == 0
