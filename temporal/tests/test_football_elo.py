"""Tests for the Elo baseline (plan step 2.3): Elo updates, the ordered-logit fit, promoted teams,
and the model run through the 2.2 walk-forward harness. No test downloads anything."""

from __future__ import annotations

import dataclasses
import math
import random
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.football.backtest import Model, MatchView, cli, run_backtest
from src.football.data import MatchRecord, parse_season_csv
from src.football.models.elo import (
    DEFAULT_LOGIT,
    EloModel,
    EloParams,
    EloRatings,
    OrderedLogit,
    OrderedLogitError,
    elo_difference,
    elo_update,
    expected_home_score,
    fit_ordered_logit,
)

FIXTURES = Path(__file__).parent / "fixtures" / "football" / "backtest"


def match(season: str, day: date, home: str, away: str, hg: int, ag: int) -> MatchRecord:
    result = "H" if hg > ag else "A" if hg < ag else "D"
    return MatchRecord(
        season=season,
        match_date=day,
        kickoff_at=None,
        home_team=home,
        away_team=away,
        home_goals=hg,
        away_goals=ag,
        result=result,  # type: ignore[arg-type]
    )


def view(season: str, home: str, away: str, day: date = date(2030, 1, 1)) -> MatchView:
    return MatchView(season=season, match_date=day, kickoff_at=None, home_team=home, away_team=away)


def assert_valid(probs) -> None:
    assert len(probs) == 3
    assert all(p >= 0.0 for p in probs)
    assert math.isclose(sum(probs), 1.0, abs_tol=1e-12)


# --- Elo updates (hand-computed) ------------------------------------------------------------
#
# Equal ratings, home advantage 60: x = 60/400 = 0.15, 10**-0.15 = 0.707946,
# E = 1 / 1.707946 = 0.585499. With K = 20 the home side gains 20 * (S - E):
#   home win  S = 1   -> +8.290026
#   draw      S = 0.5 -> -1.709974
#   away win  S = 0   -> -11.709974
# and the away side moves by the same amount the other way.


def test_expected_score_hand_computed() -> None:
    assert expected_home_score(1500, 1500, 60) == pytest.approx(0.585499, abs=1e-6)
    assert expected_home_score(1500, 1500, 0) == pytest.approx(0.5)
    # 100 points stronger, no home advantage: 1 / (1 + 10**-0.25) = 0.640065
    assert expected_home_score(1600, 1500, 0) == pytest.approx(0.640065, abs=1e-6)
    assert elo_difference(1600, 1500, 60) == pytest.approx(0.4)


@pytest.mark.parametrize(
    ("result", "change"), [("H", 8.290026), ("D", -1.709974), ("A", -11.709974)]
)
def test_elo_update_hand_computed(result: str, change: float) -> None:
    home, away = elo_update(1500, 1500, result, k=20, home_advantage=60)
    assert home == pytest.approx(1500 + change, abs=1e-6)
    assert away == pytest.approx(1500 - change, abs=1e-6)


def test_elo_update_stronger_home_win_moves_less() -> None:
    # 1 / (1 + 10**-0.25) = 0.640065 -> a win gains 20 * 0.359935 = 7.198700
    home, away = elo_update(1600, 1500, "H", k=20, home_advantage=0)
    assert home == pytest.approx(1607.198700, abs=1e-6)
    assert away == pytest.approx(1492.801300, abs=1e-6)


def test_elo_update_rejects_bad_result() -> None:
    with pytest.raises(ValueError):
        elo_update(1500, 1500, "X", k=20, home_advantage=60)


def test_engine_processes_matches_in_order() -> None:
    engine = EloRatings(EloParams(k=20, home_advantage=60))
    x = engine.process(match("1718", date(2017, 8, 12), "Arsenal", "Chelsea", 2, 0))
    assert x == pytest.approx(0.15)  # pre-match difference, both at the initial 1500
    assert engine.ratings == pytest.approx({"Arsenal": 1508.290026, "Chelsea": 1491.709974})
    engine.process(match("1718", date(2017, 8, 19), "Chelsea", "Arsenal", 1, 1))
    e = expected_home_score(1491.709974, 1508.290026, 60)
    assert engine.ratings["Chelsea"] == pytest.approx(1491.709974 + 20 * (0.5 - e), abs=1e-6)
    assert math.fsum(engine.ratings.values()) == pytest.approx(3000)  # zero-sum within a season


def test_params_validated() -> None:
    with pytest.raises(ValueError):
        EloParams(k=0)
    with pytest.raises(ValueError):
        EloParams(season_regression=1.5)
    with pytest.raises(ValueError):
        EloParams(n_relegated=0)


# --- Ordered logit --------------------------------------------------------------------------


def sample_ordered_logit(truth: OrderedLogit, n: int, seed: int) -> tuple[list[float], list[str]]:
    rng = random.Random(seed)
    xs, results = [], []
    for _ in range(n):
        x = rng.uniform(-1.0, 1.0)
        p_home, p_draw, _ = truth.probabilities(x)
        u = rng.random()
        xs.append(x)
        results.append("H" if u < p_home else "D" if u < p_home + p_draw else "A")
    return xs, results


def test_ordered_logit_recovers_known_parameters() -> None:
    truth = OrderedLogit(cut_away=-0.5, cut_draw=0.6, slope=2.5)
    xs, results = sample_ordered_logit(truth, 20000, seed=11)
    fitted = fit_ordered_logit(xs, results)
    assert fitted.cut_away == pytest.approx(truth.cut_away, abs=0.05)
    assert fitted.cut_draw == pytest.approx(truth.cut_draw, abs=0.05)
    assert fitted.slope == pytest.approx(truth.slope, abs=0.1)
    # A warm start (as used week to week) converges to the same maximum.
    warm = fit_ordered_logit(xs, results, start=OrderedLogit(-2.0, 2.0, 0.0))
    assert warm.cut_away == pytest.approx(fitted.cut_away, abs=1e-6)
    assert warm.cut_draw == pytest.approx(fitted.cut_draw, abs=1e-6)
    assert warm.slope == pytest.approx(fitted.slope, abs=1e-6)


def test_ordered_logit_negative_slope_recovered() -> None:
    truth = OrderedLogit(cut_away=-1.0, cut_draw=0.2, slope=-1.5)
    xs, results = sample_ordered_logit(truth, 20000, seed=5)
    fitted = fit_ordered_logit(xs, results)
    assert fitted.slope == pytest.approx(-1.5, abs=0.1)


def test_ordered_logit_is_a_maximum() -> None:
    xs, results = sample_ordered_logit(OrderedLogit(-0.4, 0.7, 2.0), 3000, seed=3)
    fitted = fit_ordered_logit(xs, results)

    def ll(m: OrderedLogit) -> float:
        idx = {"H": 0, "D": 1, "A": 2}
        return math.fsum(math.log(m.probabilities(x)[idx[r]]) for x, r in zip(xs, results))

    best = ll(fitted)
    nudges = ((0.02, 0, 0), (-0.02, 0, 0), (0, 0.02, 0), (0, -0.02, 0), (0, 0, 0.05), (0, 0, -0.05))
    for d in nudges:
        nudged = OrderedLogit(fitted.cut_away + d[0], fitted.cut_draw + d[1], fitted.slope + d[2])
        assert ll(nudged) < best


def test_ordered_logit_bad_warm_start_still_finds_the_maximum() -> None:
    # A wildly wrong warm start puts the observed upsets far in the tails (F rounds to 1.0 in
    # double precision); tail probabilities must stay positive so their gradient still counts.
    xs, results = sample_ordered_logit(OrderedLogit(-0.5, 0.6, 2.5), 5000, seed=9)
    cold = fit_ordered_logit(xs, results)
    bad = OrderedLogit(cut_away=-0.5, cut_draw=0.6, slope=-60.0)
    assert bad.probabilities(1.0)[0] > 0.0  # about 5e-27, not rounded to zero
    warm = fit_ordered_logit(xs, results, start=bad)
    assert warm.cut_away == pytest.approx(cold.cut_away, abs=1e-6)
    assert warm.cut_draw == pytest.approx(cold.cut_draw, abs=1e-6)
    assert warm.slope == pytest.approx(cold.slope, abs=1e-6)


def test_default_logit_is_data_free_prior() -> None:
    # Slope ln 10 (the Elo expected score's own logistic slope); trivial-model base rates between
    # equal teams at the default home advantage.
    assert DEFAULT_LOGIT.slope == pytest.approx(math.log(10), abs=0.01)
    home, draw, away = DEFAULT_LOGIT.probabilities(EloParams().home_advantage / 400)
    assert (home, draw, away) == pytest.approx((0.45, 0.27, 0.28), abs=0.005)


def test_ordered_logit_degenerate_data_rejected() -> None:
    with pytest.raises(OrderedLogitError):  # no draws: the maximum doesn't exist
        fit_ordered_logit([0.1, -0.2, 0.3], ["H", "A", "H"])
    with pytest.raises(OrderedLogitError):  # every x equal: the slope isn't identified
        fit_ordered_logit([0.0] * 6, ["H", "D", "A", "H", "D", "A"])
    with pytest.raises(ValueError):
        fit_ordered_logit([0.1], ["H", "D"])


def test_ordered_logit_parameters_validated() -> None:
    with pytest.raises(ValueError):
        OrderedLogit(cut_away=0.5, cut_draw=0.5, slope=1.0)
    with pytest.raises(ValueError):
        OrderedLogit(cut_away=0.0, cut_draw=math.nan, slope=1.0)


@pytest.mark.parametrize("x", [-50.0, -2.0, -0.3, 0.0, 0.15, 1.0, 50.0])
def test_probabilities_valid(x: float) -> None:
    assert_valid(DEFAULT_LOGIT.probabilities(x))


def test_stronger_team_and_home_advantage() -> None:
    model = EloModel()
    p_strong = model.probabilities(1650, 1500)
    p_equal = model.probabilities(1500, 1500)
    p_weak = model.probabilities(1350, 1500)
    for p in (p_strong, p_equal, p_weak):
        assert_valid(p)
    assert p_strong[0] > p_equal[0] > p_weak[0]
    assert p_strong[2] < p_equal[2] < p_weak[2]
    # Equal teams: the home side is favoured.
    assert p_equal[0] > p_equal[2]
    # A bigger home advantage, same ratings and mapping, raises the home win probability.
    no_home = EloModel(EloParams(home_advantage=0)).probabilities(1500, 1500)
    big_home = EloModel(EloParams(home_advantage=100)).probabilities(1500, 1500)
    assert no_home[0] < p_equal[0] < big_home[0]


# --- Seasons and promoted teams -------------------------------------------------------------

SEASON_ONE = ("Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot")
STRENGTH = {"Alpha": 3, "Bravo": 2, "Charlie": 2, "Delta": 1, "Echo": 1, "Foxtrot": 0}


def round_robin(season: str, teams, start: date, strength) -> list[MatchRecord]:
    """Double round robin, one match a day; the stronger team wins, equals draw."""
    out, day = [], start
    for home in teams:
        for away in teams:
            if home == away:
                continue
            diff = strength[home] - strength[away]
            hg, ag = (max(diff, 0), max(-diff, 0)) if diff else (1, 1)
            out.append(match(season, day, home, away, hg, ag))
            day += timedelta(days=1)
    return out


def test_first_season_teams_start_at_initial_rating() -> None:
    model = EloModel(EloParams(initial_rating=1400))
    model.fit([], date(2017, 8, 1))
    assert model.rating("Alpha", "1718") == 1400
    assert model.ratings() == {}
    engine = EloRatings(EloParams(initial_rating=1400))
    engine.process(match("1718", date(2017, 8, 12), "Alpha", "Bravo", 1, 1))
    # A draw between equals at home moves ratings by 20 * (0.5 - 0.585499).
    assert engine.ratings["Alpha"] == pytest.approx(1400 - 1.709974, abs=1e-6)


def test_promoted_team_starts_at_relegated_teams_mean() -> None:
    params = EloParams(n_relegated=2, season_regression=0.25, promoted_offset=-10)
    season_one = round_robin("1718", SEASON_ONE, date(2017, 8, 1), STRENGTH)
    model = EloModel(params)
    model.fit(season_one, date(2018, 8, 1))
    end = model.ratings()
    # Bottom two by points: Foxtrot (fewest), then Delta/Echo tie on points and goals, broken
    # by name (Delta first, as the table is ordered worst first by name on a full tie).
    assert end["Foxtrot"] == min(end.values())
    mean = math.fsum(end.values()) / len(end)
    regressed = {t: mean + 0.75 * (r - mean) for t, r in end.items()}
    expected_promoted = (regressed["Foxtrot"] + regressed["Delta"]) / 2 - 10
    assert model.promoted_rating("1819") == pytest.approx(expected_promoted)
    # Before the new season's first match: carried-over teams regressed, newcomers promoted.
    assert model.rating("Alpha", "1819") == pytest.approx(regressed["Alpha"])
    assert model.rating("Golf", "1819") == pytest.approx(expected_promoted)
    assert model.ratings("1819") == pytest.approx(regressed)
    assert expected_promoted < mean  # below the league average, as intended
    # After the new season starts in history, the same rating is used for the newcomer.
    season_two_first = match("1819", date(2018, 8, 11), "Golf", "Alpha", 0, 1)
    model.fit([*season_one, season_two_first], date(2018, 8, 13))
    assert model.promoted_rating() == pytest.approx(expected_promoted)
    assert model.rating("Hotel") == pytest.approx(expected_promoted)
    assert set(model.ratings()) == set(SEASON_ONE) | {"Golf"}


def test_team_absent_a_season_counts_as_promoted() -> None:
    params = EloParams(n_relegated=1)
    s1 = round_robin("1617", SEASON_ONE, date(2016, 8, 1), STRENGTH)
    s2_teams = SEASON_ONE[:-1] + ("Golf",)
    s2 = round_robin("1718", s2_teams, date(2017, 8, 1), {**STRENGTH, "Golf": 0})
    model = EloModel(params)
    model.fit([*s1, *s2], date(2018, 8, 1))
    # Foxtrot didn't play 2017/18, so returning in 2018/19 it starts as a promoted team.
    assert "Foxtrot" not in model.ratings("1819")
    assert model.rating("Foxtrot", "1819") == pytest.approx(model.promoted_rating("1819"))


# --- The model through the harness ----------------------------------------------------------


def synthetic_league(seed: int = 1) -> list[MatchRecord]:
    """Two seasons of a 10-team league with hidden strengths; 3 relegated, 3 promoted."""
    rng = random.Random(seed)
    strength = {f"T{i}": rng.gauss(0, 0.5) for i in range(13)}
    teams_by_season = {
        "1617": [f"T{i}" for i in range(10)],
        "1718": [f"T{i}" for i in range(3, 13)],
    }
    out = []
    for season, start in (("1617", date(2016, 8, 13)), ("1718", date(2017, 8, 12))):
        teams = teams_by_season[season]
        fixtures = [(h, a) for h in teams for a in teams if h != a]
        rng.shuffle(fixtures)
        for i, (h, a) in enumerate(fixtures):
            day = start + timedelta(days=7 * (i // 5) + i % 3)
            p_home = 0.15 * math.exp(0.25 + strength[h] - strength[a])
            p_away = 0.15 * math.exp(strength[a] - strength[h])
            hg = sum(rng.random() < p_home for _ in range(9))
            ag = sum(rng.random() < p_away for _ in range(9))
            out.append(match(season, day, h, a, hg, ag))
    return out


def test_implements_model_protocol_and_is_registered() -> None:
    assert isinstance(EloModel(), Model)
    assert EloModel.name == "elo"
    assert cli.MODELS["elo"] is EloModel


def test_backtest_on_fixture_files() -> None:
    records = [
        *parse_season_csv((FIXTURES / "E0_1819.csv").read_bytes(), "1819"),
        *parse_season_csv((FIXTURES / "E0_1920.csv").read_bytes(), "1920"),
    ]
    result = run_backtest(records, EloModel(), warmup_seasons=("1819",))
    assert result.model_name == "elo"
    assert len(result.predictions) == 4
    for p in result.predictions:
        assert_valid(p.model)
    assert result.overall.model is not None


def test_backtest_on_synthetic_league_fits_logit() -> None:
    records = synthetic_league()
    model = EloModel(min_fit_matches=50)
    result = run_backtest(records, model, warmup_seasons=("1617",))
    assert len(result.predictions) == 90
    assert model.logit != DEFAULT_LOGIT  # fitted, not the fallback
    assert model.logit.slope > 0
    for p in result.predictions:
        assert_valid(p.model)


def test_future_results_do_not_change_earlier_predictions() -> None:
    records = synthetic_league()
    base = run_backtest(records, EloModel(min_fit_matches=50), warmup_seasons=("1617",))
    last_window = base.predictions[-1].window_start
    flipped = [
        dataclasses.replace(
            r,
            home_goals=r.away_goals,
            away_goals=r.home_goals,
            result={"H": "A", "A": "H", "D": "D"}[r.result],  # type: ignore[index]
        )
        if r.match_date >= last_window
        else r
        for r in records
    ]
    other = run_backtest(flipped, EloModel(min_fit_matches=50), warmup_seasons=("1617",))
    # Earlier windows can't see the change, and the last window can't see its own results.
    assert [p.model for p in base.predictions] == [p.model for p in other.predictions]
    assert [p.outcome for p in base.predictions] != [p.outcome for p in other.predictions]


def test_prefix_cache_matches_fresh_fit() -> None:
    records = synthetic_league()
    cached = EloModel(min_fit_matches=50)
    cached.fit(records[:100], date(2017, 1, 1))
    cached.fit(records, date(2018, 6, 1))
    fresh = EloModel(min_fit_matches=50)
    fresh.fit(records, date(2018, 6, 1))
    assert cached.ratings() == pytest.approx(fresh.ratings())
    assert cached.logit.slope == pytest.approx(fresh.logit.slope, abs=1e-7)
    # A history that differs from the cached prefix rebuilds from scratch.
    changed = [records[0], *records[2:120]]
    cached.fit(changed, date(2017, 3, 1))
    fresh.fit(changed, date(2017, 3, 1))
    assert cached.ratings() == pytest.approx(fresh.ratings())
    assert cached.logit == fresh.logit


def test_small_history_uses_default_logit() -> None:
    model = EloModel()
    model.fit(synthetic_league()[:20], date(2016, 12, 1))
    assert model.logit == DEFAULT_LOGIT
    assert_valid(model.predict(view("1617", "T0", "T1")))
