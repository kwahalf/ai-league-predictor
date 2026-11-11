"""Unit tests of the data-quality checks (plan step 1.4): the rules, the messages, and the activity
with a fake gateway. No network, no database: the RPC's reply is built here.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from src.football.quality.activities import RPC_ROW_LIMIT, build_activities, check_quality, expected_completed_seasons
from src.football.quality.checks import (
    CHECK_NAMES,
    KNOWN_MARGIN_EXCEPTIONS,
    MARGIN_LIMITS,
    SAMPLE,
    evaluate,
    failure_message,
)
from src.football.quality.models import QUALITY_ERROR_TYPE, QualityRequest

CURRENT = "2627"
COMPLETED = [f"{y:02d}{y + 1:02d}" for y in range(16, 26)]


def _season(season: str, completed: bool = True, matches: int = 380, teams: int = 20, **extra: Any) -> dict:
    row = {
        "season": season, "completed": completed, "matches": matches, "teams": teams,
        "max_home": 19, "max_away": 19, "unfinished": 0, "unfinished_rows": [],
        "teams_off": [], "teams_off_count": 0, "missing_pairs": 0, "missing_pair_rows": [],
    }
    row.update(extra)
    return row


def _empty() -> dict:
    return {"count": 0, "rows": []}


def good_facts() -> dict:
    """What the RPC returns for clean data: ten whole seasons and a partial current one."""
    return {
        "seasons": [_season(s) for s in COMPLETED]
        + [_season(CURRENT, completed=False, matches=100, max_home=5, max_away=5)],
        "duplicate_fixtures": _empty(),
        "scores": _empty(),
        "odds_prices": _empty(),
        "odds_margins": _empty(),
        "totals": {"matches": 3900, "odds": 26940},
    }


def _margin_row(market="1x2", prices=(1.5, 3.0, 4.0), margin=0.25, **extra) -> dict:
    row = {
        "market": market, "season": "6970", "date": "2069-08-20", "home": "Arsenal", "away": "Chelsea",
        "bookmaker": "B365", "snapshot": "pre_match", "prices": list(prices), "margin": margin,
    }
    row.update(extra)
    return row


def _known_row() -> dict:
    """The real 2022/23 Bet365 source error, as the RPC returns it."""
    return _margin_row(
        season="2223", date="2023-02-18", home="Aston Villa", away="Arsenal", prices=(4.5, 3.6, 1.5),
        margin=0.166667,
    )


def _judge(facts: dict):
    return evaluate(facts, division="E0", current_season=CURRENT)


def _check(report, name):
    return next(c for c in report.checks if c.name == name)


class FakeGateway:
    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.calls: list[tuple[str, dict]] = []

    def rpc(self, function: str, args: dict) -> Any:
        self.calls.append((function, args))
        return copy.deepcopy(self.reply)


# --- rules ---------------------------------------------------------------------------------------


def test_clean_data_passes_every_check():
    report = _judge(good_facts())
    assert [c.name for c in report.checks] == list(CHECK_NAMES)
    assert all(c.passed for c in report.checks)
    assert report.failed == []
    assert (report.matches_checked, report.odds_checked) == (3900, 26940)


def test_a_completed_season_with_a_missing_match_names_the_teams_and_the_pair():
    facts = good_facts()
    facts["seasons"][3] = _season(
        "1920", matches=379, teams_off_count=2,
        teams_off=[{"team": "Arsenal", "home": 19, "away": 18}, {"team": "Chelsea", "home": 18, "away": 19}],
        missing_pairs=1, missing_pair_rows=[{"home": "Chelsea", "away": "Arsenal"}],
    )
    check = _check(_judge(facts), "completed_seasons")
    assert not check.passed and check.count == 1
    assert check.message == (
        "1 seasons: season 1920: 379 matches, expected 380; 2 teams without 19 home and 19 away matches: "
        "Arsenal 37 (19 home, 18 away), Chelsea 37 (18 home, 19 away); "
        "1 home/away pairs with no match: Chelsea v Arsenal"
    )


def test_a_completed_season_with_a_wrong_team_count_or_an_unplayed_match_fails():
    facts = good_facts()
    facts["seasons"][0] = _season("1617", teams=21)
    facts["seasons"][1] = _season(
        "1718", unfinished=1,
        unfinished_rows=[{"date": "2018-05-13", "home": "Arsenal", "away": "Burnley", "status": "scheduled"}],
    )
    check = _check(_judge(facts), "completed_seasons")
    assert check.count == 2
    assert "season 1617: 21 teams, expected 20" in check.message
    assert "season 1718: 1 matches not finished: 2018-05-13 Arsenal v Burnley (scheduled)" in check.message


def test_a_completed_season_with_no_rows_fails():
    facts = good_facts()
    facts["seasons"][0] = _season("1617", matches=0, teams=0, max_home=0, max_away=0)
    check = _check(_judge(facts), "completed_seasons")
    assert "season 1617: 0 matches, expected 380; 0 teams, expected 20" in check.message


def test_the_current_season_may_be_partial_but_not_oversized():
    facts = good_facts()
    assert _check(_judge(facts), "current_season").passed
    facts["seasons"][-1] = _season(CURRENT, completed=False, matches=120, teams=21, max_home=20, max_away=6)
    check = _check(_judge(facts), "current_season")
    assert not check.passed
    assert "season 2627: 21 teams, more than 20; a team has 20 home / 6 away matches, more than 19" in check.message
    # The current season's shape is never judged by the completed-season rule.
    assert _check(_judge(facts), "completed_seasons").passed


def test_duplicate_fixtures_are_named():
    facts = good_facts()
    facts["duplicate_fixtures"] = {
        "count": 2,
        "rows": [
            {"kind": "same_season", "season": "2324", "home": "Arsenal", "away": "Chelsea", "matches": 2},
            {"kind": "same_day", "date": "2069-08-20",
             "a": {"season": "6869", "home": "Arsenal", "away": "Chelsea"},
             "b": {"season": "6970", "home": "Arsenal", "away": "Chelsea"}},
        ],
    }
    check = _check(_judge(facts), "duplicate_fixtures")
    assert check.message == (
        "2 duplicates: season 2324 Arsenal v Chelsea stored 2 times; "
        "2069-08-20: 6869 Arsenal v Chelsea and 6970 Arsenal v Chelsea share a team on the same day"
    )


def test_scores_that_dont_fit_the_status_are_named():
    facts = good_facts()
    facts["scores"] = {"count": 2, "rows": [
        {"season": "2324", "date": "2024-01-01", "home": "Arsenal", "away": "Chelsea",
         "status": "postponed", "home_goals": 1, "away_goals": 0},
        {"season": "2324", "date": "2024-01-02", "home": "Everton", "away": "Fulham",
         "status": "finished", "home_goals": None, "away_goals": None},
    ]}
    check = _check(_judge(facts), "scores_match_status")
    assert "2324 2024-01-01 Arsenal v Chelsea: status postponed, score 1-0" in check.message
    assert "2324 2024-01-02 Everton v Fulham: status finished, score ?-?" in check.message


def test_prices_of_one_or_less_are_named():
    facts = good_facts()
    facts["odds_prices"] = {"count": 1, "rows": [{
        "season": "2122", "date": "2021-12-19", "home": "Newcastle United", "away": "Manchester City",
        "bookmaker": "B365", "snapshot": "closing",
        "prices": {"home": 9.0, "draw": 5.5, "away": 1.3, "over_2_5": 0, "under_2_5": 1.0},
    }]}
    check = _check(_judge(facts), "odds_above_one")
    assert check.message == (
        "1 odds rows: 2122 2021-12-19 Newcastle United v Manchester City B365 closing: over_2_5=0, under_2_5=1.0"
    )


def test_margins_outside_the_limits_fail_per_market():
    facts = good_facts()
    facts["odds_margins"] = {"count": 2, "rows": [
        _margin_row(),
        _margin_row(market="over_under_2_5", prices=(2.5, 2.5), margin=-0.2, date="2069-09-10"),
    ]}
    check = _check(_judge(facts), "odds_margins")
    assert check.count == 2
    assert check.message.startswith("2 price sets outside 1X2 0-15%, over/under 2.5 0-15%: ")
    assert "6970 2069-08-20 Arsenal v Chelsea B365 pre_match 1X2 1.5/3/4: margin 25.00%" in check.message
    assert "over/under 2.5 2.5/2.5: margin -20.00%" in check.message


def test_the_known_source_error_is_acknowledged_not_failed():
    facts = good_facts()
    facts["odds_margins"] = {"count": 1, "rows": [_known_row()]}
    report = _judge(facts)
    assert _check(report, "odds_margins").passed
    assert len(report.acknowledged) == 1 and "Aston Villa v Arsenal B365 pre_match" in report.acknowledged[0]


def test_the_known_source_error_only_matches_its_exact_prices():
    facts = good_facts()
    changed = _known_row()
    changed["prices"] = [4.5, 3.6, 1.45]
    facts["odds_margins"] = {"count": 1, "rows": [changed]}
    report = _judge(facts)
    assert not _check(report, "odds_margins").passed
    assert report.acknowledged == []


def test_a_new_bad_margin_still_fails_next_to_the_known_one():
    facts = good_facts()
    facts["odds_margins"] = {"count": 2, "rows": [_known_row(), _margin_row()]}
    check = _check(_judge(facts), "odds_margins")
    assert check.count == 1 and "Arsenal v Chelsea" in check.message and "Aston Villa" not in check.message


def test_messages_are_capped_at_the_sample_size():
    facts = good_facts()
    facts["scores"] = {"count": 25, "rows": [
        {"season": "2324", "date": f"2024-01-{d:02d}", "home": "A", "away": "B", "status": "scheduled",
         "home_goals": 1, "away_goals": 1}
        for d in range(1, 21)
    ]}
    check = _check(_judge(facts), "scores_match_status")
    assert check.count == 25
    assert check.message.count("status scheduled") == SAMPLE
    assert check.message.endswith(f"(first {SAMPLE} of 25)")


def test_failure_message_lists_every_failed_check():
    facts = good_facts()
    facts["seasons"][0] = _season("1617", matches=379)
    facts["odds_margins"] = {"count": 1, "rows": [_margin_row()]}
    message = failure_message(_judge(facts))
    assert message.startswith("Data-quality check failed for E0 (current season 2627): 2 of 6 checks failed. ")
    assert "completed_seasons: 1 seasons: season 1617: 379 matches" in message
    assert " | odds_margins: 1 price sets" in message


def test_margin_limits_are_zero_to_fifteen_percent_for_both_markets():
    assert MARGIN_LIMITS == {"1x2": (0.0, 0.15), "over_under_2_5": (0.0, 0.15)}
    assert len(KNOWN_MARGIN_EXCEPTIONS) == 1


# --- season arithmetic ---------------------------------------------------------------------------


def test_completed_seasons_are_first_season_up_to_the_one_before_the_current():
    assert expected_completed_seasons("2627") == COMPLETED
    assert expected_completed_seasons("1718") == ["1617"]
    assert expected_completed_seasons("1617") == []
    assert expected_completed_seasons("0001") == []
    with pytest.raises(ValueError):
        expected_completed_seasons("2016")


# --- activity with a fake gateway ----------------------------------------------------------------


def test_the_activity_sends_the_rules_and_returns_the_report():
    gateway = FakeGateway(good_facts())
    report = check_quality(gateway, QualityRequest(current_season=CURRENT))
    assert report.failed == []
    ((function, args),) = gateway.calls
    assert function == "football_quality_check"
    assert args == {
        "p_division": "E0",
        "p_current_season": CURRENT,
        "p_expected_seasons": COMPLETED,
        "p_teams_per_season": 20,
        "p_margin_limits": {"1x2": {"min": 0.0, "max": 0.15}, "over_under_2_5": {"min": 0.0, "max": 0.15}},
        "p_limit": RPC_ROW_LIMIT,
    }


def test_a_failed_check_raises_a_non_retryable_data_quality_error():
    facts = good_facts()
    facts["scores"] = {"count": 1, "rows": [
        {"season": "2324", "date": "2024-01-01", "home": "Arsenal", "away": "Chelsea",
         "status": "postponed", "home_goals": 1, "away_goals": 0}]}
    with pytest.raises(ApplicationError) as info:
        check_quality(FakeGateway(facts), QualityRequest(current_season=CURRENT))
    assert info.value.type == QUALITY_ERROR_TYPE
    assert info.value.non_retryable
    assert "scores_match_status: 1 matches: 2324 2024-01-01 Arsenal v Chelsea" in info.value.message


def test_a_malformed_reply_is_a_non_retryable_error_but_not_a_failed_check():
    with pytest.raises(ApplicationError) as info:
        check_quality(FakeGateway({"seasons": []}), QualityRequest(current_season=CURRENT))
    assert info.value.type == "QualityCheckError" and info.value.non_retryable


def test_a_bad_request_never_reaches_the_database():
    gateway = FakeGateway(good_facts())
    for request in (QualityRequest(current_season="2016"), QualityRequest(current_season=CURRENT, division="SP1")):
        with pytest.raises(ApplicationError) as info:
            check_quality(gateway, request)
        assert info.value.type == "BadRequest" and info.value.non_retryable
    assert gateway.calls == []


def test_build_activities_registers_the_check_under_its_name():
    (act,) = build_activities(FakeGateway(good_facts()))
    assert act.__temporal_activity_definition.name == "football_quality_check"
