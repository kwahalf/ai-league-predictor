"""Tests for the walk-forward backtest harness (plan step 2.2), including the no-leakage tests.

The no-leakage tests are the most important tests in the suite (spec, "Testing Strategy"): a
backtest that peeks at results or closing odds looks better than reality.

Fixtures in tests/fixtures/football/backtest/ are small synthetic season files in the real layouts:
- E0_1819.csv: 2 matches (pre-2019/20 layout: no Time, BbAv columns, Pinnacle closing only).
- E0_1920.csv: 4 matches over 2 weeks (Time, Avg and closing columns). Prices are chosen so the
  margin-free probabilities are exact: 2/4/4 -> (0.5, 0.25, 0.25). Match 3 has a B365 home price
  of 0 (treated as missing); match 4 has no model-input pre-match odds, only Pinnacle pre-match
  (benchmark-only) and closing odds, so it has no market benchmark.
No test downloads anything.
"""

from __future__ import annotations

import dataclasses
import math
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

import pytest

from src.football.backtest import (
    MODEL_ODDS_SOURCES,
    TRIVIAL_PROBABILITIES,
    BacktestError,
    MatchView,
    TrivialModel,
    format_report,
    history_record,
    market_consensus,
    match_view,
    model_input_odds,
    run_backtest,
    week_start,
)
from src.football.backtest import cli
from src.football.data import MatchRecord, OddsQuote, parse_season_csv

FIXTURES = Path(__file__).parent / "fixtures" / "football" / "backtest"
OUTCOME_FIELDS = (
    "home_goals",
    "away_goals",
    "result",
    "home_shots",
    "away_shots",
    "home_shots_on_target",
    "away_shots_on_target",
    "home_corners",
    "away_corners",
    "home_yellow_cards",
    "away_yellow_cards",
    "home_red_cards",
    "away_red_cards",
)


def fixture_records() -> list[MatchRecord]:
    return [
        *parse_season_csv((FIXTURES / "E0_1819.csv").read_bytes(), "1819"),
        *parse_season_csv((FIXTURES / "E0_1920.csv").read_bytes(), "1920"),
    ]


# --- Synthetic data -------------------------------------------------------------------------

TEAMS = ("Arsenal", "Chelsea", "Everton", "Liverpool", "Man City", "Man United", "Spurs", "Wolves")


def quote(source, snapshot, h, d, a, over=None, under=None) -> OddsQuote:
    return OddsQuote(source, snapshot, h, d, a, over, under)


def make_match(
    day: date,
    home: str,
    away: str,
    *,
    season: str = "1718",
    result: str | None = "H",
    kickoff: datetime | None = None,
    odds: tuple[OddsQuote, ...] = (),
) -> MatchRecord:
    goals = {"H": (2, 0), "D": (1, 1), "A": (0, 1), None: (None, None)}[result]
    return MatchRecord(
        season=season,
        match_date=day,
        kickoff_at=kickoff,
        home_team=home,
        away_team=away,
        home_goals=goals[0],
        away_goals=goals[1],
        result=result,  # type: ignore[arg-type]
        home_shots=10 if result else None,
        away_shots=8 if result else None,
        odds=odds,
    )


def _random_prices(rng: random.Random) -> tuple[float, float, float]:
    return (
        round(rng.uniform(1.2, 6), 2),
        round(rng.uniform(2.8, 5), 2),
        round(rng.uniform(1.3, 9), 2),
    )


def synthetic_seasons(seed: int = 7) -> list[MatchRecord]:
    """Two short seasons ("1617" warm-up, "1718" scored) with every odds snapshot and source,
    matches on every day of the week, date-only and timed kick-offs, and some bad prices."""
    rng = random.Random(seed)
    records: list[MatchRecord] = []
    for season, first in (("1617", date(2017, 3, 4)), ("1718", date(2017, 8, 11))):
        for i in range(60):
            day = first + timedelta(days=(i * 3) // 2)  # several matches on some dates
            home, away = rng.sample(TEAMS, 2)
            timed = season == "1718" and i % 2 == 0
            kickoff = (
                datetime(day.year, day.month, day.day, 14 + i % 6, 0, tzinfo=timezone.utc)
                if timed
                else None
            )
            odds = [
                quote("B365", "pre_match", *_random_prices(rng), 1.9, 1.9),
                quote("BW", "pre_match", *_random_prices(rng)),
                quote("AVG", "pre_match", *_random_prices(rng), 1.95, 1.85),
                quote("PS", "pre_match", *_random_prices(rng)),
                quote("B365", "closing", *_random_prices(rng), 1.8, 2.0),
                quote("BW", "closing", *_random_prices(rng)),
                quote("AVG", "closing", *_random_prices(rng)),
                quote("PS", "closing", *_random_prices(rng)),
            ]
            if i % 7 == 0:
                odds[0] = quote("B365", "pre_match", 0.0, 3.4, 2.5)  # bad price: treat as missing
            if i % 11 == 0:
                odds = [q for q in odds if q.snapshot == "closing" or q.source == "PS"]
            records.append(
                make_match(
                    day,
                    home,
                    away,
                    season=season,
                    result=rng.choice("HHHDDA"),
                    kickoff=kickoff,
                    odds=tuple(odds),
                )
            )
    return records


def scramble_closing(records: Sequence[MatchRecord], seed: int) -> list[MatchRecord]:
    """Same matches with every closing quote (and Pinnacle pre-match) replaced by random prices."""
    rng = random.Random(seed)
    out = []
    for r in records:
        odds = tuple(
            quote(q.source, q.snapshot, *_random_prices(rng), rng.uniform(1.2, 3), 1.5)
            if q.snapshot == "closing" or q.source == "PS"
            else q
            for q in r.odds
        )
        out.append(dataclasses.replace(r, odds=odds))
    return out


# --- Test models ----------------------------------------------------------------------------


class RecordingModel:
    """Records every argument the harness passes; predicts a constant."""

    name = "recording"

    def __init__(self) -> None:
        self.fits: list[tuple[date, tuple[MatchRecord, ...]]] = []
        self.predicted: list[tuple[date, MatchView]] = []

    def fit(self, history, as_of):
        self.fits.append((as_of, tuple(history)))

    def predict(self, match):
        self.predicted.append((self.fits[-1][0], match))
        return (0.4, 0.3, 0.3)


class OddsAndResultsModel:
    """Uses everything a model may use: the match's pre-match odds, plus results and every odds
    price in its history. Any leak of closing odds or future results changes its output."""

    name = "odds-and-results"

    def fit(self, history, as_of):
        n = len(history)
        home_rate = sum(r.result == "H" for r in history) / n if n else 0.45
        price_sum = math.fsum(
            p
            for r in history
            for q in r.odds
            for p in (q.home, q.draw, q.away, q.over_2_5, q.under_2_5)
            if p is not None
        )
        self.tilt = 0.2 * home_rate + 0.05 * math.sin(price_sum)

    def predict(self, match):
        market = market_consensus(match.odds) or (0.45, 0.27, 0.28)
        home = 0.6 * market[0] + self.tilt
        rest = 1.0 - home
        share = market[1] / (market[1] + market[2])
        return (home, rest * share, rest * (1 - share))


# --- Week boundary --------------------------------------------------------------------------


def test_week_start_is_monday():
    assert week_start(date(2019, 8, 5)) == date(2019, 8, 5)  # Monday
    assert week_start(date(2019, 8, 9)) == date(2019, 8, 5)  # Friday
    assert week_start(date(2019, 8, 11)) == date(2019, 8, 5)  # Sunday
    assert week_start(date(2019, 8, 12)) == date(2019, 8, 12)  # next Monday


# --- What a model may see -------------------------------------------------------------------


def test_match_view_has_no_outcome_fields():
    names = {f.name for f in dataclasses.fields(MatchView)}
    assert names == {"season", "match_date", "kickoff_at", "home_team", "away_team", "odds"}
    # slots: nothing can be smuggled onto a view as an extra attribute.
    view = match_view(fixture_records()[2])
    assert not hasattr(view, "__dict__")
    for field in OUTCOME_FIELDS:
        assert not hasattr(view, field)


def test_model_input_odds_keeps_pre_match_input_sources_only():
    odds = (
        quote("B365", "pre_match", 2.0, 0.0, 1.0, 1.9, 1.9),
        quote("BW", "closing", 2.0, 3.0, 4.0),
        quote("PS", "pre_match", 2.0, 3.0, 4.0),
        quote("AVG", "live", 2.0, 3.0, 4.0),
        quote("AVG", "pre_match", 0.0, 1.0, 0.5),  # nothing usable left: dropped
    )
    assert model_input_odds(odds) == (quote("B365", "pre_match", 2.0, None, None, 1.9, 1.9),)


def test_history_record_keeps_results_but_strips_closing_odds():
    record = fixture_records()[2]  # Liverpool v Norwich
    seen = history_record(record)
    assert (seen.result, seen.home_goals, seen.away_goals, seen.home_shots) == ("H", 4, 1, 15)
    assert {(q.source, q.snapshot) for q in seen.odds} == {
        ("B365", "pre_match"),
        ("BW", "pre_match"),
        ("AVG", "pre_match"),
    }


def test_market_consensus_skips_missing_and_bad_prices():
    assert market_consensus(
        (quote("B365", "pre_match", 0.0, 3.5, 2.0), quote("BW", "pre_match", 4.0, 4.0, 2.0))
    ) == pytest.approx((0.25, 0.25, 0.5))
    assert market_consensus((quote("PS", "pre_match", 2.0, 4.0, 4.0),)) is None
    assert market_consensus((quote("B365", "closing", 2.0, 4.0, 4.0),)) is None
    assert market_consensus(()) is None
    # Equal-weight average of two margin-free vectors.
    assert market_consensus(
        (quote("B365", "pre_match", 2.0, 4.0, 4.0), quote("AVG", "pre_match", 4.0, 2.0, 4.0))
    ) == pytest.approx((0.375, 0.375, 0.25))


# --- No-leakage tests -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "records, warmup",
    [(fixture_records(), ("1819",)), (synthetic_seasons(), ("1617",))],
    ids=["fixture-files", "synthetic"],
)
def test_model_never_sees_results_closing_odds_or_the_window(records, warmup):
    model = RecordingModel()
    result = run_backtest(records, model, warmup_seasons=warmup)

    assert model.fits, "the model was never fitted"
    for as_of, history in model.fits:
        assert as_of.weekday() == 0, "windows start on a Monday"
        for r in history:
            assert isinstance(r, MatchRecord)
            assert r.match_date < as_of, "history must end strictly before the window"
            assert r.result is not None
            for q in r.odds:
                assert q.snapshot == "pre_match", "closing odds must never reach a model"
                assert q.source in MODEL_ODDS_SOURCES, "Pinnacle is benchmark-only"
                prices = (q.home, q.draw, q.away, q.over_2_5, q.under_2_5)
                assert all(p is None or p > 1.0 for p in prices)

    for as_of, view in model.predicted:
        assert type(view) is MatchView, "a predicted match must only be seen as a MatchView"
        for field in OUTCOME_FIELDS:
            assert not hasattr(view, field)
        assert as_of <= view.match_date < as_of + timedelta(days=7)
        for q in view.odds:
            assert q.snapshot == "pre_match"
            assert q.source in MODEL_ODDS_SOURCES

    # Every non-warm-up match is predicted exactly once; no warm-up match is predicted.
    predicted = sorted((v.match_date, v.home_team, v.away_team) for _, v in model.predicted)
    expected = sorted(
        (r.match_date, r.home_team, r.away_team) for r in records if r.season not in warmup
    )
    assert predicted == expected
    assert len(result.predictions) == len(expected)


def test_predictions_ignore_closing_odds():
    """Spec: feed odds where only the closing columns disagree; predictions must not change."""
    records = synthetic_seasons()
    base = run_backtest(records, OddsAndResultsModel())
    for seed in (1, 2, 3):
        scrambled = run_backtest(scramble_closing(records, seed), OddsAndResultsModel())
        assert [p.model for p in scrambled.predictions] == [p.model for p in base.predictions]
        assert [p.market for p in scrambled.predictions] == [p.market for p in base.predictions]
        assert scrambled.overall == base.overall

    # Sanity: the model really does use odds, so the invariance above means something.
    changed = [
        dataclasses.replace(
            r,
            odds=tuple(
                dataclasses.replace(q, home=q.home + 0.5)
                if q.snapshot == "pre_match" and q.home and q.home > 1.0
                else q
                for q in r.odds
            ),
        )
        for r in records
    ]
    moved = run_backtest(changed, OddsAndResultsModel())
    assert [p.model for p in moved.predictions] != [p.model for p in base.predictions]


def test_predictions_never_use_results_on_or_after_their_window():
    """Changing every result from date D on leaves every window that starts by D unchanged."""
    records = synthetic_seasons()
    cutoff = date(2017, 9, 18)  # a Monday inside the scored season
    flip = {"H": "A", "D": "H", "A": "D"}
    altered = [
        make_match(
            r.match_date,
            r.home_team,
            r.away_team,
            season=r.season,
            result=flip[r.result],  # type: ignore[index]
            kickoff=r.kickoff_at,
            odds=r.odds,
        )
        if r.match_date >= cutoff
        else r
        for r in records
    ]
    base = run_backtest(records, OddsAndResultsModel()).predictions
    other = run_backtest(altered, OddsAndResultsModel()).predictions
    before = [(a.model, b.model) for a, b in zip(base, other) if a.window_start <= cutoff]
    after = [(a.model, b.model) for a, b in zip(base, other) if a.window_start > cutoff]
    assert before and all(a == b for a, b in before)
    assert after and any(a != b for a, b in after), "sanity: the model does use results"


def test_same_date_results_are_not_in_history():
    """Date-only matches on the same day as a predicted match must not be visible to it, even
    when one of them 'happened first'. Timed kick-offs on the same day are treated the same way."""
    sat = date(2017, 8, 12)
    records = [
        make_match(date(2017, 5, 21), "Arsenal", "Everton", season="1617"),
        make_match(sat, "Chelsea", "Spurs", result="A"),
        make_match(sat, "Liverpool", "Wolves", result="D"),
        make_match(
            sat, "Man City", "Everton", kickoff=datetime(2017, 8, 12, 11, 30, tzinfo=timezone.utc)
        ),
        make_match(
            sat, "Man United", "Arsenal", kickoff=datetime(2017, 8, 12, 16, 30, tzinfo=timezone.utc)
        ),
        make_match(date(2017, 8, 13), "Spurs", "Wolves"),  # Sunday, same window
        make_match(date(2017, 8, 14), "Everton", "Chelsea"),  # Monday: next window
    ]
    model = RecordingModel()
    run_backtest(records, model)

    seen_by = {}
    for as_of, view in model.predicted:
        history = dict(model.fits)[as_of]
        seen_by[(view.home_team, view.away_team)] = {(r.home_team, r.away_team) for r in history}
    for match in [("Chelsea", "Spurs"), ("Liverpool", "Wolves"), ("Man City", "Everton"),
                  ("Man United", "Arsenal"), ("Spurs", "Wolves")]:  # fmt: skip
        assert seen_by[match] == {("Arsenal", "Everton")}
    # The Monday match sees the whole previous week, including the Sunday.
    assert seen_by[("Everton", "Chelsea")] == {
        ("Arsenal", "Everton"),
        ("Chelsea", "Spurs"),
        ("Liverpool", "Wolves"),
        ("Man City", "Everton"),
        ("Man United", "Arsenal"),
        ("Spurs", "Wolves"),
    }


# --- Scores ---------------------------------------------------------------------------------

# Trivial forecast (0.45, 0.27, 0.28); cumulative (0.45, 0.72).
TRIVIAL_RPS = {
    "H": (0.55**2 + 0.28**2) / 2,  # 0.19045
    "D": (0.45**2 + 0.28**2) / 2,  # 0.14045
    "A": (0.45**2 + 0.72**2) / 2,  # 0.36045
}
TRIVIAL_BRIER = {
    "H": 0.55**2 + 0.27**2 + 0.28**2,  # 0.4538
    "D": 0.45**2 + 0.73**2 + 0.28**2,  # 0.8138
    "A": 0.45**2 + 0.27**2 + 0.72**2,  # 0.7938
}


def test_trivial_model_on_fixture_files_gives_hand_checked_scores():
    result = run_backtest(fixture_records(), TrivialModel(), warmup_seasons=("1819",))

    assert result.n_windows == 2  # weeks starting Mon 5 Aug and Mon 12 Aug 2019
    assert [p.window_start for p in result.predictions] == [date(2019, 8, 5)] * 2 + [
        date(2019, 8, 12)
    ] * 2
    assert all(p.model == TRIVIAL_PROBABILITIES for p in result.predictions)
    assert [p.outcome for p in result.predictions] == [0, 1, 2, 0]  # H, D, A, H

    # Market: match 1 (0.5, 0.25, 0.25) from all three sources; match 2 (0.25, 0.5, 0.25) from B365
    # and AVG (BW blank); match 3 (0.25, 0.25, 0.5) from BW only (B365 home price is 0, AVG blank);
    # match 4 has only Pinnacle pre-match and closing odds, so no benchmark.
    markets = [p.market for p in result.predictions]
    assert markets[0] == pytest.approx((0.5, 0.25, 0.25))
    assert markets[1] == pytest.approx((0.25, 0.5, 0.25))
    assert markets[2] == pytest.approx((0.25, 0.25, 0.5))
    assert markets[3] is None

    (season,) = result.seasons
    assert season.season == "1920"
    assert season == dataclasses.replace(result.overall, season="1920")
    assert (season.n_predicted, season.n_scored, season.n_without_market) == (4, 4, 1)
    outcomes = "HDAH"
    assert season.model.n == 4
    assert season.model.rps == pytest.approx(sum(TRIVIAL_RPS[o] for o in outcomes) / 4)  # 0.22045
    assert season.model.rps == pytest.approx(0.22045)
    assert season.model.brier == pytest.approx(sum(TRIVIAL_BRIER[o] for o in outcomes) / 4)
    assert season.model.log_loss == pytest.approx(
        -(2 * math.log(0.45) + math.log(0.27) + math.log(0.28)) / 4
    )
    # Market RPS: H with (0.5, 0.25, 0.25): (0.25 + 0.0625) / 2 = 0.15625;
    # D with (0.25, 0.5, 0.25): (0.0625 + 0.0625) / 2 = 0.0625; A with (0.25, 0.25, 0.5): 0.15625.
    assert season.market.n == 3
    assert season.market.rps == pytest.approx((0.15625 + 0.0625 + 0.15625) / 3)  # 0.125
    assert season.market.log_loss == pytest.approx(-(3 * math.log(0.5)) / 3)
    assert season.model_on_market_matches.rps == pytest.approx(
        (TRIVIAL_RPS["H"] + TRIVIAL_RPS["D"] + TRIVIAL_RPS["A"]) / 3
    )  # 0.23045

    # Calibration: 12 model forecasts; 0.45 x4 (2 hit), 0.27 x4 (1 hit), 0.28 x4 (1 hit).
    by_bucket = {b.lower: b for b in result.model_calibration if b.count}
    assert by_bucket[0.4].count == 4 and by_bucket[0.4].observed_frequency == pytest.approx(0.5)
    assert by_bucket[0.2].count == 8 and by_bucket[0.2].observed_frequency == pytest.approx(0.25)
    assert sum(b.count for b in result.market_calibration) == 9


def test_matches_without_a_result_are_predicted_but_not_scored():
    records = [
        make_match(date(2017, 5, 1), "Arsenal", "Everton", season="1617"),
        make_match(date(2017, 8, 12), "Chelsea", "Spurs", result="D"),
        make_match(date(2017, 8, 19), "Wolves", "Liverpool", result=None),
    ]
    result = run_backtest(records, TrivialModel())
    assert result.overall.n_predicted == 2
    assert result.overall.n_scored == 1
    assert result.overall.model.rps == pytest.approx(TRIVIAL_RPS["D"])
    assert result.overall.market is None
    assert result.overall.n_without_market == 1


def test_invalid_predictions_fail_loudly():
    class Bad(TrivialModel):
        name = "bad"

    records = fixture_records()
    for probs in [(0.5, 0.5), (0.5, 0.3, 0.3), (1.2, -0.1, -0.1), (float("nan"), 0.5, 0.5), None]:
        model = Bad(probs)  # type: ignore[arg-type]
        with pytest.raises(BacktestError, match="model 'bad' on 2019-08-09 Liverpool v Norwich"):
            run_backtest(records, model, warmup_seasons=("1819",))


def test_nothing_to_predict_fails():
    with pytest.raises(BacktestError, match="no matches outside the warm-up seasons"):
        run_backtest(fixture_records(), TrivialModel(), warmup_seasons=("1819", "1920"))


def test_report_lists_each_season_and_the_market():
    report = format_report(
        run_backtest(fixture_records(), TrivialModel(), warmup_seasons=("1819",))
    )
    assert "model=trivial" in report
    assert "warm-up 2018/19" in report
    assert "2019/20" in report
    assert "0.2205" in report  # model RPS
    assert "0.1250" in report  # market RPS
    assert "0.2305" in report  # model RPS on the market's matches (0.23045)
    assert "Calibration, market consensus:" in report


def test_report_keeps_exact_calibration_bucket_edges():
    result = run_backtest(fixture_records(), TrivialModel(), warmup_seasons=("1819",), n_buckets=20)
    report = format_report(result)
    for label in ("[0.0, 0.05)", "[0.05, 0.1)", "[0.25, 0.3)", "[0.45, 0.5)", "[0.95, 1]"):
        assert label in report


# --- Command line ---------------------------------------------------------------------------


def test_cli_uses_cached_files_without_downloading(tmp_path, monkeypatch, capsys):
    for season in ("1819", "1920"):
        (tmp_path / f"E0_{season}.csv").write_bytes((FIXTURES / f"E0_{season}.csv").read_bytes())

    def no_network(*args, **kwargs):
        raise AssertionError("must not download a cached season")

    monkeypatch.setattr(cli.urllib.request, "urlopen", no_network)
    monkeypatch.setattr(cli, "SEASONS", ("1819", "1920"))
    monkeypatch.setattr(cli, "DEFAULT_WARMUP_SEASONS", ("1819",))
    assert cli.main(["--model", "trivial", "--cache-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "2019/20" in out and "0.2205" in out


def test_cli_downloads_missing_season_into_cache(tmp_path, monkeypatch):
    payload = (FIXTURES / "E0_1920.csv").read_bytes()
    requested = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return payload

    def fake_urlopen(request, timeout):
        requested.append(request.full_url)
        return Response()

    monkeypatch.setattr(cli.urllib.request, "urlopen", fake_urlopen)
    cache = tmp_path / "cache"
    path = cli.download_season("1920", cache)
    assert requested == ["https://www.football-data.co.uk/mmz4281/1920/E0.csv"]
    assert path == cache / "E0_1920.csv" and path.read_bytes() == payload
    assert [p.name for p in cache.iterdir()] == ["E0_1920.csv"]  # no partial file left behind
    cli.download_season("1920", cache)
    assert len(requested) == 1  # cached
    cli.download_season("1920", cache, refresh=True)
    assert len(requested) == 2


def test_cli_season_list_is_the_ten_completed_seasons():
    assert cli.SEASONS[0] == "1617" and cli.SEASONS[-1] == "2526" and len(cli.SEASONS) == 10
    assert cli.DEFAULT_CACHE_DIR.parts[-3:] == ("temporal", ".cache", "football-data")
