"""Tests for the football-data.co.uk CSV parser (plan step 1.2).

Fixture files in tests/fixtures/football/ are verbatim rows cut from the real files on 2026-11-11
(git stores them with LF line endings; the originals are CRLF, covered by a synthetic test below):
- E0_1617_sample.csv: 2016/17 (no BOM, dd/mm/yy dates, no Time, BbAv columns, no closing odds
  except Pinnacle's).
- E0_2324_sample.csv: 2023/24 (Time, Avg columns, closing odds), including the BST start day.
- fixtures_sample.csv: fixtures.csv with BOM, two non-E0 rows and its trailing all-comma row.
- team_names.json: every distinct team spelling across all 11 season files and fixtures.csv.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.football.data import (
    TEAM_ALIASES,
    CsvFormatError,
    MatchRecord,
    OddsQuote,
    UnknownLayoutError,
    UnknownTeamError,
    canonical_team,
    parse_fixtures_csv,
    parse_season_csv,
)

FIXTURES = Path(__file__).parent / "fixtures" / "football"
UTC = timezone.utc


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _quotes(match: MatchRecord) -> dict[tuple[str, str], tuple]:
    return {
        (q.source, q.snapshot): (q.home, q.draw, q.away, q.over_2_5, q.under_2_5)
        for q in match.odds
    }


# --- Real-data fixture files ------------------------------------------------------------------


def test_pre_2019_season_parses_to_expected_rows():
    matches = parse_season_csv(_read("E0_1617_sample.csv"), "1617")

    assert [(m.match_date, m.home_team, m.away_team) for m in matches] == [
        (date(2016, 8, 13), "Burnley", "Swansea City"),
        (date(2016, 8, 13), "Manchester City", "Sunderland"),
        (date(2016, 12, 26), "Arsenal", "West Bromwich Albion"),
        (date(2017, 5, 21), "Hull City", "Tottenham Hotspur"),
    ]
    first = matches[0]
    assert first == MatchRecord(
        season="1617",
        match_date=date(2016, 8, 13),
        kickoff_at=None,  # no Time column before 2019/20
        home_team="Burnley",
        away_team="Swansea City",
        home_goals=0,
        away_goals=1,
        result="A",
        home_shots=10,
        away_shots=17,
        home_shots_on_target=3,
        away_shots_on_target=9,
        home_corners=7,
        away_corners=4,
        home_yellow_cards=3,
        away_yellow_cards=2,
        home_red_cards=0,
        away_red_cards=0,
        odds=(
            OddsQuote("B365", "pre_match", 2.4, 3.3, 3.25, None, None),
            OddsQuote("BW", "pre_match", 2.45, 3.1, 2.95, None, None),
            OddsQuote("AVG", "pre_match", 2.43, 3.21, 3.1, 2.3, 1.61),
            OddsQuote("PS", "pre_match", 2.47, 3.32, 3.19, None, None),
            OddsQuote("PS", "closing", 2.79, 3.16, 2.89, None, None),
        ),
    )


def test_pre_2019_has_no_closing_odds_except_pinnacle():
    for match in parse_season_csv(_read("E0_1617_sample.csv"), "1617"):
        snapshots = {(q.source, q.snapshot) for q in match.odds}
        assert snapshots == {
            ("B365", "pre_match"),
            ("BW", "pre_match"),
            ("AVG", "pre_match"),
            ("PS", "pre_match"),
            ("PS", "closing"),
        }
        assert match.kickoff_at is None


def test_pre_2019_last_row_values():
    hull = parse_season_csv(_read("E0_1617_sample.csv"), "1617")[-1]
    assert (hull.home_goals, hull.away_goals, hull.result) == (1, 7, "A")
    assert (hull.home_corners, hull.away_corners) == (2, 3)
    assert _quotes(hull)[("AVG", "pre_match")] == (6.09, 4.4, 1.53, 1.6, 2.32)
    assert _quotes(hull)[("PS", "closing")] == (9.74, 6.48, 1.31, None, None)


def test_post_2019_season_parses_to_expected_rows():
    matches = parse_season_csv(_read("E0_2324_sample.csv"), "2324")
    assert len(matches) == 3

    burnley = matches[0]
    assert burnley.season == "2324"
    assert (burnley.home_team, burnley.away_team) == ("Burnley", "Manchester City")
    assert (burnley.home_goals, burnley.away_goals, burnley.result) == (0, 3, "A")
    assert (burnley.home_red_cards, burnley.away_red_cards) == (1, 0)
    # 20:00 BST on 11 Aug 2023 is 19:00 UTC.
    assert burnley.kickoff_at == datetime(2023, 8, 11, 19, 0, tzinfo=UTC)
    assert burnley.kickoff_at.tzinfo is not None
    assert _quotes(burnley) == {
        ("B365", "pre_match"): (8.0, 5.5, 1.33, 1.67, 2.2),
        ("B365", "closing"): (9.0, 5.25, 1.33, 1.67, 2.2),
        ("BW", "pre_match"): (8.75, 5.25, 1.34, None, None),
        ("BW", "closing"): (8.75, 5.25, 1.33, None, None),
        ("AVG", "pre_match"): (9.02, 5.35, 1.35, 1.65, 2.27),
        ("AVG", "closing"): (9.27, 5.45, 1.33, 1.64, 2.28),
        ("PS", "pre_match"): (8.58, 5.51, 1.37, 1.68, 2.29),
        ("PS", "closing"): (9.62, 5.81, 1.33, 1.65, 2.35),
    }


def test_kickoff_converted_from_uk_time_across_gmt_and_bst():
    _, boxing_day, bst_start = parse_season_csv(_read("E0_2324_sample.csv"), "2324")
    # 26 Dec: GMT, so 20:00 local == 20:00 UTC.
    assert boxing_day.kickoff_at == datetime(2023, 12, 26, 20, 0, tzinfo=UTC)
    # 31 Mar 2024 is the day BST began: 16:30 local == 15:30 UTC.
    assert (bst_start.home_team, bst_start.away_team) == ("Manchester City", "Arsenal")
    assert bst_start.match_date == date(2024, 3, 31)
    assert bst_start.kickoff_at == datetime(2024, 3, 31, 15, 30, tzinfo=UTC)


def test_fixtures_file_filters_division_and_uses_live_snapshot():
    matches = parse_fixtures_csv(_read("fixtures_sample.csv"), "2627")

    assert [(m.home_team, m.away_team) for m in matches] == [
        ("Everton", "Coventry City"),
        ("Leeds United", "Tottenham Hotspur"),
    ]
    everton = matches[0]
    assert everton.season == "2627"
    assert everton.kickoff_at == datetime(2026, 11, 6, 20, 0, tzinfo=UTC)  # GMT
    assert (everton.home_goals, everton.away_goals, everton.result) == (None, None, None)
    assert everton.home_shots is None and everton.away_red_cards is None
    assert _quotes(everton) == {
        ("B365", "live"): (1.7, 4.0, 4.75, 1.73, 2.1),
        ("BW", "live"): (1.68, 4.0, 4.75, None, None),
        ("AVG", "live"): (1.67, 3.95, 4.79, 1.69, 2.09),
    }
    assert everton.odds_for("AVG", "live") == OddsQuote("AVG", "live", 1.67, 3.95, 4.79, 1.69, 2.09)
    assert everton.odds_for("PS", "live") is None
    assert matches[1].kickoff_at == datetime(2026, 11, 7, 12, 30, tzinfo=UTC)


def test_text_and_bytes_input_give_the_same_result():
    raw = _read("fixtures_sample.csv")
    assert raw.startswith(b"\xef\xbb\xbf")  # the real file has a BOM
    from_bytes = parse_fixtures_csv(raw, "2627")
    from_text = parse_fixtures_csv(raw.decode("utf-8"), "2627")  # BOM kept as U+FEFF
    assert from_bytes == from_text


# --- Team aliases ------------------------------------------------------------------------------


def test_every_team_in_all_real_files_resolves():
    doc = json.loads((FIXTURES / "team_names.json").read_text(encoding="utf-8"))
    names = doc["names"]
    assert len(doc["by_file"]) == 12  # 11 seasons + fixtures.csv
    assert set(names) == {n for per_file in doc["by_file"].values() for n in per_file}
    resolved = {name: canonical_team(name) for name in names}
    # Every canonical club is used by some real spelling (no dead entries in the table).
    assert set(resolved.values()) == set(TEAM_ALIASES)


def test_canonical_team_is_tolerant_of_case_and_whitespace():
    assert canonical_team("Man United") == "Manchester United"
    assert canonical_team("  man   united ") == "Manchester United"
    assert canonical_team("Manchester United") == "Manchester United"
    assert canonical_team("Nott'm Forest") == "Nottingham Forest"


def test_unknown_team_raises_naming_the_team():
    with pytest.raises(UnknownTeamError, match="Real Madrid") as info:
        canonical_team("Real Madrid")
    assert info.value.name == "Real Madrid"


def test_unknown_team_in_file_raises():
    text = "Div,Date,HomeTeam,AwayTeam,B365H,B365D,B365A\nE0,13/08/16,Burnley,Gotham FC,2,3,4\n"
    with pytest.raises(UnknownTeamError, match="Gotham FC"):
        parse_season_csv(text, "1617")


# --- Edge cases ---------------------------------------------------------------------------------

_MINIMAL_HEADER = "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,B365H,B365D,B365A"


def test_bom_in_text_and_bytes():
    text = "\ufeff" + _MINIMAL_HEADER + "\nE0,01/09/2024,16:30,Arsenal,Brighton,1,1,D,1.5,4,6\n"
    for data in (text, text.encode("utf-8")):
        (match,) = parse_season_csv(data, "2425")
        assert match.home_team == "Arsenal"
        assert match.kickoff_at == datetime(2024, 9, 1, 15, 30, tzinfo=UTC)


@pytest.mark.parametrize("raw_date", ["13/08/16", "13/08/2016"])
def test_two_and_four_digit_years(raw_date):
    text = (
        "Div,Date,HomeTeam,AwayTeam,B365H,B365D,B365A\n"
        f"E0,{raw_date},Burnley,Swansea,2.4,3.3,3.25\n"
    )
    (match,) = parse_season_csv(text, "1617")
    assert match.match_date == date(2016, 8, 13)


def test_missing_time_column_gives_date_only():
    text = "Div,Date,HomeTeam,AwayTeam,B365H,B365D,B365A\nE0,13/08/16,Burnley,Swansea,2.4,3.3,3.25\n"
    (match,) = parse_season_csv(text, "1617")
    assert match.kickoff_at is None
    assert match.match_date == date(2016, 8, 13)


def test_blank_time_value_gives_date_only():
    text = _MINIMAL_HEADER + "\nE0,01/09/2024,,Arsenal,Brighton,,,,1.5,4,6\n"
    (match,) = parse_season_csv(text, "2425")
    assert match.kickoff_at is None
    assert (match.home_goals, match.away_goals, match.result) == (None, None, None)


def test_missing_individual_columns_are_none_and_empty_sources_dropped():
    text = "Div,Date,HomeTeam,AwayTeam,AvgH,AvgD,AvgA,BWH,BWD,BWA\nE0,13/08/16,Burnley,Swansea,2.4,,3.25,,,\n"
    (match,) = parse_season_csv(text, "1617")
    assert match.odds == (OddsQuote("AVG", "pre_match", 2.4, None, 3.25, None, None),)
    assert match.home_shots is None


def test_trailing_blank_rows_and_unnamed_columns_ignored():
    text = (
        "Div,Date,HomeTeam,AwayTeam,B365H,B365D,B365A,,\r\n"
        "E0,13/08/16,Burnley,Swansea,2.4,3.3,3.25,,\r\n"
        ",,,,,,,,\r\n"
        "\r\n"
    )
    matches = parse_season_csv(text, "1617")
    assert len(matches) == 1


def test_unknown_layout_raises():
    text = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,XYZH,XYZD,XYZA\nE0,13/08/16,Burnley,Swansea,0,1,2,3,4\n"
    with pytest.raises(UnknownLayoutError, match="no recognised 1X2 odds columns"):
        parse_season_csv(text, "1617")


def test_missing_basic_columns_raises():
    with pytest.raises(UnknownLayoutError, match="HomeTeam"):
        parse_season_csv("Div,Date,Home,Away,B365H,B365D,B365A\n", "1617")


def test_empty_input_raises_unknown_layout():
    with pytest.raises(UnknownLayoutError):
        parse_season_csv(b"", "1617")


def test_wrong_division_in_season_file_raises():
    text = "Div,Date,HomeTeam,AwayTeam,B365H,B365D,B365A\nE1,13/08/16,Burnley,Swansea,2.4,3.3,3.25\n"
    with pytest.raises(CsvFormatError, match="division 'E1'"):
        parse_season_csv(text, "1617")


@pytest.mark.parametrize(
    "row, message",
    [
        ("E0,2016-08-13,Burnley,Swansea,2.4,3.3,3.25", "Date"),
        ("E0,13/08/16,Burnley,Swansea,evens,3.3,3.25", "B365H"),
        ("E0,13/08/16,Burnley,Swansea,nan,3.3,3.25", "B365H"),
        ("E0,13/08/16,Burnley,Swansea,2.4,inf,3.25", "B365D"),
        ("E0,13/08/16,Burnley,Swansea,2.4,3.3,3.5,3.25", "more values than header columns"),
    ],
)
def test_bad_values_raise_with_line_number(row, message):
    text = "Div,Date,HomeTeam,AwayTeam,B365H,B365D,B365A\n" + row + "\n"
    with pytest.raises(CsvFormatError, match=f"line 2: {message}"):
        parse_season_csv(text, "1617")


def test_invalid_season_rejected():
    with pytest.raises(ValueError, match="season"):
        parse_season_csv(_read("E0_1617_sample.csv"), "2016/17")


def test_empty_surplus_fields_allowed():
    text = "Div,Date,HomeTeam,AwayTeam,B365H,B365D,B365A\nE0,13/08/16,Burnley,Swansea,2.4,3.3,3.25,,\n"
    (match,) = parse_season_csv(text, "1617")
    assert match.odds_for("B365", "pre_match").away == 3.25


def test_duplicate_column_names_raise():
    text = "Div,Date,HomeTeam,AwayTeam,B365H,B365D,B365A, B365H\nE0,13/08/16,Burnley,Swansea,2.4,3.3,3.25,9.0\n"
    with pytest.raises(UnknownLayoutError, match="duplicate column names \\['B365H'\\]"):
        parse_season_csv(text, "1617")


def test_unterminated_quote_raises_instead_of_swallowing_rows():
    text = (
        "Div,Date,HomeTeam,AwayTeam,B365H,B365D,B365A,Referee\n"
        'E0,13/08/16,Burnley,Swansea,2.4,3.3,3.25,"J Moss\n'
        "E0,13/08/16,Man City,Sunderland,1.25,6.5,15,R Madley\n"
    )
    with pytest.raises(CsvFormatError, match="malformed CSV"):
        parse_season_csv(text, "1617")


def test_quoted_fields_still_parse():
    text = (
        "Div,Date,HomeTeam,AwayTeam,B365H,B365D,B365A,Referee\n"
        'E0,13/08/16,"Nott\'m Forest",Swansea,2.4,3.3,3.25,"Moss, J"\n'
    )
    (match,) = parse_season_csv(text, "1617")
    assert match.home_team == "Nottingham Forest"
