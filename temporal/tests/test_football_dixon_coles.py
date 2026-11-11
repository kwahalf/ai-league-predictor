"""Tests for the Dixon–Coles fundamentals model (plan step 2.4).

- Score matrix: sums to 1, the markets derived from it agree, τ only touches the four low cells.
- Fit: recovers known parameters on synthetic data; recent matches move it more than old ones;
  the promoted-team prior behaves as documented; home advantage is positive on a real season.
- It runs through the 2.2 backtest harness on the existing fixtures and never reads odds.

No test downloads anything. The real-season test uses the 2023/24 Premier League results embedded
below (``SEASON_2324``), taken from football-data.co.uk's E0 file.
"""

from __future__ import annotations

import dataclasses
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from src.football.backtest import MatchView, TrivialModel, run_backtest
from src.football.backtest import cli
from src.football.data import MatchRecord, OddsQuote, parse_season_csv
from src.football.models.dixon_coles import fit as dc_fit
from src.football.models.dixon_coles import (
    DEFAULT_PROMOTED_ATTACK,
    DEFAULT_PROMOTED_DEFENCE,
    DixonColesModel,
    ScoreForecast,
    score_matrix,
    tau_factors,
)

FIXTURES = Path(__file__).parent / "fixtures" / "football"


def record(day: date, home: str, away: str, hg: int, ag: int, season: str = "2324") -> MatchRecord:
    result = "H" if hg > ag else "A" if ag > hg else "D"
    return MatchRecord(season, day, None, home, away, home_goals=hg, away_goals=ag, result=result)


# --- Score matrix ---------------------------------------------------------------------------

RATES = [(1.5, 1.1, -0.1), (0.4, 3.2, 0.05), (2.6, 0.7, -0.2), (1.0, 1.0, 0.0), (3.5, 2.5, -0.05)]


@pytest.mark.parametrize("lam,mu,rho", RATES)
def test_score_matrix_sums_to_one(lam: float, mu: float, rho: float) -> None:
    matrix = score_matrix(lam, mu, rho)
    assert matrix.shape == (11, 11)
    assert abs(matrix.sum() - 1.0) <= 1e-9
    assert (matrix >= 0).all()


@pytest.mark.parametrize("lam,mu,rho", RATES)
def test_markets_derived_from_the_matrix_agree(lam: float, mu: float, rho: float) -> None:
    fc = ScoreForecast.from_rates(lam, mu, rho)
    m = fc.matrix
    cells = [(i, j, float(m[i, j])) for i in range(11) for j in range(11)]
    home, draw, away = fc.outcome_probabilities()
    over, under = fc.over_under(2.5)
    btts_yes, btts_no = fc.btts()

    assert home == pytest.approx(sum(p for i, j, p in cells if i > j), abs=1e-12)
    assert draw == pytest.approx(sum(p for i, j, p in cells if i == j), abs=1e-12)
    assert away == pytest.approx(sum(p for i, j, p in cells if i < j), abs=1e-12)
    assert abs(home + draw + away - 1.0) <= 1e-12
    assert under == pytest.approx(sum(p for i, j, p in cells if i + j <= 2), abs=1e-12)
    assert over + under == pytest.approx(1.0, abs=1e-12)
    assert btts_yes == pytest.approx(sum(p for i, j, p in cells if i >= 1 and j >= 1), abs=1e-12)
    assert btts_no == pytest.approx(m[0, :].sum() + m[:, 0].sum() - m[0, 0], abs=1e-12)

    # Cross-market identities: every under-2.5 score is a 0-0, 1-1, a win by one or two with no
    # away goal, or the mirror; BTTS-and-under is exactly 1-1; a draw under 2.5 is 0-0 or 1-1.
    under_draw = m[0, 0] + m[1, 1]
    under_home = m[1, 0] + m[2, 0]
    under_away = m[0, 1] + m[0, 2]
    assert under == pytest.approx(under_draw + under_home + under_away, abs=1e-12)
    assert draw >= under_draw and home >= under_home and away >= under_away
    assert btts_yes - m[1, 1] <= over + 1e-12  # BTTS with 3+ goals is part of over 2.5
    assert btts_no >= under - m[1, 1] - 1e-12  # every under score except 1-1 has a blank side

    # The matrix's expected goals are λ and μ: τ preserves both means, only truncation differs.
    # (The truncated matrix's means fall short of λ and μ by under 0.3%, even at a rate of 3.5.)
    goals = np.arange(11)
    assert float(goals @ m.sum(axis=1)) == pytest.approx(lam, rel=3e-3)
    assert float(goals @ m.sum(axis=0)) == pytest.approx(mu, rel=3e-3)
    assert fc.expected_goals == (lam, mu)


def test_tau_only_changes_the_four_low_score_cells() -> None:
    lam, mu, rho = 1.4, 1.2, -0.13
    plain = score_matrix(lam, mu, 0.0)
    corrected = score_matrix(lam, mu, rho)
    # τ preserves the total over the infinite grid, so the two normalising constants agree to
    # within the truncated mass; compare the ratio of the matrices cell by cell.
    ratio = corrected / plain
    scale = ratio[2:, :].mean()
    expected = np.full_like(ratio, scale)
    expected[:2, :2] *= tau_factors(lam, mu, rho)
    np.testing.assert_allclose(ratio, expected, rtol=1e-12)
    assert scale == pytest.approx(1.0, abs=1e-6)
    assert not np.allclose(corrected[:2, :2], plain[:2, :2])
    # Negative ρ (the usual Premier League sign) raises 0-0 and 1-1, lowers 1-0 and 0-1.
    assert corrected[0, 0] > plain[0, 0] and corrected[1, 1] > plain[1, 1]
    assert corrected[1, 0] < plain[1, 0] and corrected[0, 1] < plain[0, 1]


def test_top_scorelines_are_sorted_and_come_from_the_matrix() -> None:
    fc = ScoreForecast.from_rates(1.6, 0.9, -0.1)
    top = fc.top_scorelines(5)
    assert len(top) == 5
    probs = [s.probability for s in top]
    assert probs == sorted(probs, reverse=True)
    assert probs[0] == pytest.approx(float(fc.matrix.max()))
    for s in top:
        assert s.probability == pytest.approx(float(fc.matrix[s.home_goals, s.away_goals]))


def test_score_matrix_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        score_matrix(0.0, 1.0)
    for line in (2.0, -0.5, -1.5):
        with pytest.raises(ValueError):
            ScoreForecast.from_rates(1.0, 1.0).over_under(line)
    over, under = ScoreForecast.from_rates(1.0, 1.0).over_under(0.5)
    assert under == pytest.approx(score_matrix(1.0, 1.0)[0, 0])


def test_negative_tau_is_floored_at_zero() -> None:
    matrix = score_matrix(3.0, 3.0, 0.3)  # τ(0, 0) = 1 − 9·0.3 < 0
    assert matrix[0, 0] == 0.0
    assert abs(matrix.sum() - 1.0) <= 1e-9 and (matrix >= 0).all()


# --- Synthetic leagues ----------------------------------------------------------------------


def simulate_league(
    attack: np.ndarray,
    defence: np.ndarray,
    home_adv: float,
    rho: float,
    *,
    rounds: int,
    start: date,
    seed: int,
    season: str = "2324",
) -> list[MatchRecord]:
    """``rounds`` double round robins, one round of matches a week, scores drawn from the exact
    Dixon–Coles score matrix of each fixture."""
    rng = np.random.default_rng(seed)
    n = len(attack)
    teams = [f"Team {i:02d}" for i in range(n)]
    records: list[MatchRecord] = []
    day = start
    for _ in range(rounds):
        for h in range(n):
            for a in range(n):
                if h == a:
                    continue
                lam = math.exp(home_adv + attack[h] - defence[a])
                mu = math.exp(attack[a] - defence[h])
                m = score_matrix(lam, mu, rho).ravel()
                cell = int(rng.choice(m.size, p=m / m.sum()))
                records.append(record(day, teams[h], teams[a], cell // 11, cell % 11, season))
            day += timedelta(days=7)
    return records


def test_fit_recovers_known_parameters() -> None:
    rng = np.random.default_rng(7)
    n = 20
    attack = rng.normal(0.0, 0.3, n)
    attack -= attack.mean()  # the fit's identifiability constraint
    defence = rng.normal(-0.1, 0.3, n)
    home_adv, rho = 0.25, -0.12
    records = simulate_league(
        attack, defence, home_adv, rho, rounds=10, start=date(2023, 8, 7), seed=11
    )  # 3,800 matches

    model = DixonColesModel(half_life_days=None)
    model.fit(records, date(2030, 1, 1))
    p = model.parameters
    assert p.converged and p.n_matches == len(records)
    fitted_attack = np.array([p.attack[f"Team {i:02d}"] for i in range(n)])
    fitted_defence = np.array([p.defence[f"Team {i:02d}"] for i in range(n)])

    assert abs(fitted_attack.mean()) < 1e-9
    assert np.abs(fitted_attack - attack).max() < 0.15
    assert np.abs(fitted_defence - defence).max() < 0.15
    assert np.sqrt(np.mean((fitted_attack - attack) ** 2)) < 0.06
    assert np.sqrt(np.mean((fitted_defence - defence) ** 2)) < 0.06
    assert p.home_adv == pytest.approx(home_adv, abs=0.04)
    assert p.rho == pytest.approx(rho, abs=0.05)


def _numeric_gradient(f, theta: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    grad = np.zeros_like(theta)
    for i in range(theta.size):
        step = np.zeros_like(theta)
        step[i] = eps
        grad[i] = (f(theta + step) - f(theta - step)) / (2 * eps)
    return grad


def test_analytic_gradient_matches_finite_differences() -> None:
    rng = np.random.default_rng(0)
    n, m = 6, 300
    home = rng.integers(0, n, m)
    away = (home + rng.integers(1, n, m)) % n
    data = dc_fit.FitData(
        home, away, rng.integers(0, 4, m).astype(float), rng.integers(0, 4, m).astype(float),
        rng.random(m), n,
    )  # fmt: skip
    prior = dc_fit.TeamPrior(rng.normal(size=n), rng.normal(size=n), rng.random(n) * 5)
    theta = np.concatenate([rng.normal(0, 0.3, 2 * n), [0.25, -0.1]])
    for p in (None, prior):
        value = lambda t: dc_fit._objective(t, data, p, 3.0)[0]  # noqa: E731
        analytic = dc_fit._objective(theta, data, p, 3.0)[1]
        np.testing.assert_allclose(analytic, _numeric_gradient(value, theta), atol=1e-6)


def test_gradient_is_zero_through_a_floored_tau() -> None:
    # One 0-0 between two teams with λ = μ = 2 and ρ = 0.28: τ(0, 0) = 1 − 4·0.28 < 0 is floored,
    # so the correction is locally constant and contributes nothing to the gradient.
    data = dc_fit.FitData(
        np.array([0]), np.array([1]), np.array([0.0]), np.array([0.0]), np.array([1.0]), 2
    )
    theta = np.array([0.0, 0.0, -np.log(2.0), -np.log(2.0), 0.0, 0.28])
    value = lambda t: dc_fit._objective(t, data, None, 1.0)[0]  # noqa: E731
    analytic = dc_fit._objective(theta, data, None, 1.0)[1]
    assert analytic[-1] == 0.0
    np.testing.assert_allclose(analytic, _numeric_gradient(value, theta), atol=1e-6)


def _base_league() -> list[MatchRecord]:
    n = 10
    rng = np.random.default_rng(3)
    attack = rng.normal(0.0, 0.2, n)
    defence = rng.normal(0.0, 0.2, n)
    return simulate_league(attack, defence, 0.2, -0.1, rounds=2, start=date(2023, 8, 7), seed=5)


def test_recent_matches_move_the_fit_more_than_old_ones() -> None:
    base = _base_league()
    first, last = base[0].match_date, base[-1].match_date
    as_of = last + timedelta(days=1)
    team = "Team 03"

    def attack_with_thrashing_on(day: date) -> float:
        extra = [record(day, team, "Team 07", 6, 0), record(day, team, "Team 08", 6, 0)]
        model = DixonColesModel(half_life_days=60)
        model.fit(base + extra, as_of)
        return model.parameters.attack[team]

    reference = DixonColesModel(half_life_days=60)
    reference.fit(base, as_of)
    baseline = reference.parameters.attack[team]

    recent_shift = attack_with_thrashing_on(last) - baseline
    old_shift = attack_with_thrashing_on(first) - baseline
    assert recent_shift > 0 and old_shift > 0
    assert recent_shift > 3 * old_shift

    # Without decay, the date of a match doesn't matter.
    def attack_without_decay(day: date) -> float:
        extra = [record(day, team, "Team 07", 6, 0), record(day, team, "Team 08", 6, 0)]
        model = DixonColesModel(half_life_days=None)
        model.fit(base + extra, as_of)
        return model.parameters.attack[team]

    assert attack_without_decay(last) == pytest.approx(attack_without_decay(first), abs=1e-6)


def test_fit_uses_only_the_last_n_seasons_before_as_of() -> None:
    old = [record(date(2019, 9, 1), "A", "B", 9, 0, season="1920")]
    seasons = [
        record(date(2020 + k, 9, 1), "A", "B", 1, 1, season=f"{20 + k}{21 + k}") for k in range(3)
    ]
    future = [record(date(2024, 1, 1), "A", "B", 9, 0, season="2324")]
    model = DixonColesModel(n_seasons=3)
    model.fit(old + seasons + future, date(2023, 6, 1))
    assert model.parameters.seasons == ("2021", "2122", "2223")
    assert model.parameters.n_matches == 3


# --- Promoted teams -------------------------------------------------------------------------


def _two_seasons_with_promoted_team(promoted_goals: tuple[int, int]) -> list[MatchRecord]:
    """Season 2223 between ten teams; in 2324 'Newcomer' replaces 'Team 09' and plays 3 home
    matches, all with score ``promoted_goals``."""
    n = 10
    rng = np.random.default_rng(1)
    last = simulate_league(
        rng.normal(0, 0.2, n), rng.normal(0, 0.2, n), 0.2, -0.1,
        rounds=2, start=date(2022, 8, 8), seed=2, season="2223",
    )  # fmt: skip
    saturdays = [date(2023, 8, 12) + timedelta(days=7 * k) for k in range(8)]
    this = [record(day, f"Team {k:02d}", f"Team {k + 1:02d}", 1, 1) for k, day in enumerate(saturdays)]
    this += [
        record(day + timedelta(days=1), "Newcomer", f"Team {k:02d}", *promoted_goals)
        for k, day in enumerate(saturdays[:3])
    ]
    return last + this


def test_promoted_team_is_fitted_with_the_prior() -> None:
    history = _two_seasons_with_promoted_team((3, 0))
    as_of = date(2023, 9, 4)

    with_prior = DixonColesModel()
    with_prior.fit(history, as_of)
    without = DixonColesModel(promoted_precision=0.0)
    without.fit(history, as_of)

    assert with_prior.parameters.promoted == {"Newcomer"}
    assert "Team 00" not in with_prior.parameters.promoted
    assert "Team 09" not in with_prior.parameters.current_teams

    # Three 3-0 home wins: unregularised, the newcomer looks elite; the prior pulls its attack
    # back toward the promoted-team mean and its defence toward the league mean + offset.
    p, q = with_prior.parameters, without.parameters
    assert q.attack["Newcomer"] > p.attack["Newcomer"] > DEFAULT_PROMOTED_ATTACK
    rel_def = lambda params: params.defence["Newcomer"] - params.mean_defence  # noqa: E731
    assert rel_def(q) > rel_def(p) > DEFAULT_PROMOTED_DEFENCE
    # Established teams are barely affected by the newcomer's prior.
    assert p.attack["Team 00"] == pytest.approx(q.attack["Team 00"], abs=0.05)


def test_prior_fades_as_the_promoted_team_plays_more() -> None:
    def pull(n_matches: int) -> float:
        history = _two_seasons_with_promoted_team((3, 0))
        history += [
            record(date(2023, 10, 1) + timedelta(days=7 * k), "Newcomer", f"Team {k % 9:02d}", 3, 0)
            for k in range(n_matches)
        ]
        as_of = history[-1].match_date + timedelta(days=1)
        with_prior, without = DixonColesModel(), DixonColesModel(promoted_precision=0.0)
        with_prior.fit(history, as_of)
        without.fit(history, as_of)
        return without.parameters.attack["Newcomer"] - with_prior.parameters.attack["Newcomer"]

    assert pull(30) < pull(0) / 2


def test_promotion_is_detected_when_the_window_is_one_season() -> None:
    history = _two_seasons_with_promoted_team((3, 0))
    model = DixonColesModel(n_seasons=1)
    model.fit(history, date(2023, 9, 4))
    assert model.parameters.seasons == ("2324",)
    assert model.parameters.promoted == {"Newcomer"}


def test_returning_team_keeps_the_prior_until_its_first_result() -> None:
    # "Team 09" plays 2223, is absent in 2324 ("Newcomer" replaces it) and returns in 2425, where
    # other teams have already played. "Team 01" played 2324 but its first 2425 match is delayed.
    history = _two_seasons_with_promoted_team((1, 1))
    history += [
        record(date(2024, 8, 17), "Team 02", "Team 03", 2, 1, season="2425"),
        record(date(2024, 8, 17), "Newcomer", "Team 04", 0, 0, season="2425"),
    ]
    model = DixonColesModel()
    model.fit(history, date(2024, 8, 19))
    p = model.parameters
    assert p.seasons[-1] == "2425" and "Team 09" in p.attack
    prior = (DEFAULT_PROMOTED_ATTACK, p.mean_defence + DEFAULT_PROMOTED_DEFENCE)
    assert model.strengths("Team 09", "2425") == pytest.approx(prior)
    assert model.strengths("Team 01", "2425") == (p.attack["Team 01"], p.defence["Team 01"])
    assert model.strengths("Team 02", "2425") == (p.attack["Team 02"], p.defence["Team 02"])

    # After its first 2425 result it is fitted, with the promoted-team prior in the fit.
    history.append(record(date(2024, 8, 18), "Team 09", "Team 05", 1, 0, season="2425"))
    model.fit(history, date(2024, 8, 19))
    p = model.parameters
    assert "Team 09" in p.promoted
    assert model.strengths("Team 09", "2425") == (p.attack["Team 09"], p.defence["Team 09"])


def test_unknown_and_returning_teams_get_the_promoted_prior() -> None:
    history = _two_seasons_with_promoted_team((1, 1))
    model = DixonColesModel()
    model.fit(history, date(2023, 9, 4))
    p = model.parameters
    prior = (DEFAULT_PROMOTED_ATTACK, p.mean_defence + DEFAULT_PROMOTED_DEFENCE)

    # Never seen: the prior.
    assert model.strengths("Brand New FC", "2324") == pytest.approx(prior)
    # Relegated last season (no 2324 match) and predicted next season: back to the prior.
    assert model.strengths("Team 09", "2425") == pytest.approx(prior)
    # Within the fitted season it counts as established (it played the season before) and simply
    # hasn't played yet, so its fitted strengths apply.
    assert model.strengths("Team 09", "2324") == (p.attack["Team 09"], p.defence["Team 09"])
    # Teams in the current league keep their fitted strengths into the next season.
    assert model.strengths("Team 00", "2425") == (p.attack["Team 00"], p.defence["Team 00"])

    # The prior is below average: a promoted side is expected to lose to an average team at home
    # more than an average side would.
    fc = model.forecast("Brand New FC", "Team 00", "2324")
    lam, mu = fc.expected_goals
    avg_home = math.exp(p.home_adv + 0.0 - p.defence["Team 00"])
    assert lam < avg_home


# --- Real-like data -------------------------------------------------------------------------

TEAMS_2324 = (
    "Arsenal", "Aston Villa", "Bournemouth", "Brentford", "Brighton & Hove Albion", "Burnley",
    "Chelsea", "Crystal Palace", "Everton", "Fulham", "Liverpool", "Luton Town", "Manchester City",
    "Manchester United", "Newcastle United", "Nottingham Forest", "Sheffield United",
    "Tottenham Hotspur", "West Ham United", "Wolverhampton Wanderers",
)  # fmt: skip
SEASON_2324_START = date(2023, 8, 11)
# Every 2023/24 Premier League result. Each token is: day offset from SEASON_2324_START (2 base-36
# digits), home team index, away team index (into TEAMS_2324, base 36), home goals, away goals.
SEASON_2324 = (
    "005c03 010f21 012i11 014b41 018901 01g701 01e151 023h22 026a11 03dj10 07fg21 089303 08a231 "
    "08j414 08hd20 08ce10 091840 09i631 0a7001 0e6b30 0f2h02 0f0922 0f3711 0f8j01 0fdf32 0f4i13 "
    "0g5113 0ggc12 0gea12 0lbi12 0mg822 0m3222 0m5h25 0m6f01 0mc951 0m4e31 0n7j32 0na130 0n0d31 "
    "10ja13 101731 109b10 10d413 10hg21 10ic13 10e310 112600 118001 12f511 177900 17bj11 17cf20 "
    "173813 175d01 180h22 184231 186101 18ai31 18ge08 1e1461 1e2004 1e8b12 1ed701 1ee520 1eig20 "
    "1ejc21 1eha21 1ff311 1g9602 1hb512 1lbh01 1l5614 1l8230 1l9g31 1ld321 1l7f00 1m4a22 1mie22 "
    "1mj111 1m0c10 1za820 1z2j12 1z3530 1zc421 1ze740 1zfb22 1z6022 1zgd12 201i41 21h920 257h12 "
    "266302 260g50 262521 26je22 27i801 271b31 274911 27af30 27dc03 2d9d01 2d3i32 2d5702 2d8411 "
    "2dc261 2dgj21 2de010 2ef120 2eba11 2fh614 2kjh21 2k0531 2k7823 2kdb10 2k2e20 2l1931 2l4g11 "
    "2la330 2lif32 2l6c44 2yca11 2y5i12 2yb721 2ye641 2yf423 2yg213 2y3001 2zh112 2z8d03 309j32 "
    "350j21 353b31 355g50 35f801 35ed10 362122 366432 36a943 36i711 36ch33 38j510 38b034 394321 "
    "397202 399f50 39ga02 391c10 39d621 3a8e30 3ahi12 3c7a12 3c4511 3cd203 3cg310 3cjf11 3c1010 "
    "3d8620 3d9i50 3dbc12 3dhe41 3ifh02 3j6g20 3jc722 3je930 3j5802 3k0420 3k3112 3kij30 3kad00 "
    "3o7411 3p1g11 3qid20 3q9502 3qbe10 3qf223 3qh821 3qa011 3rj621 3tef13 3t2930 3tgb23 3t5a02 "
    "3td132 3u3j14 3u6721 3u8c13 3v4h42 3v0i02 3xb623 3x1532 3x7331 3xcg20 3xj830 3xfd21 3y9021 "
    "3yh231 3zae42 40i400 4a5b11 4b6910 4bec23 4c8100 4cdh22 4i0750 4i3f32 4jgi22 4j2a04 4k4j00 "
    "4sf012 4s9800 4sb440 4s7g32 4s1e13 4tc531 4th332 4ta641 4ui211 4ujd34 4w8h22 4w4741 4w5922 "
    "4web44 4wg105 4x2f11 4x6j24 4xdi30 4x0a31 4y3c13 53c820 539231 53a531 53bg13 53h421 53j302 "
    "53fe23 54i006 541d12 557613 5a3a14 5a5005 5a9112 5ae222 5afi20 5ahj12 5ac611 5bg405 5bbd12 "
    "5c8711 5dc310 5eab41 5h1f42 5h4811 5h7530 5hd912 5h2c01 5h0e41 5ijg10 5ji342 5o3622 5o8i13 "
    "5o9430 5oej30 5ofa01 5oh731 5ob123 5p5202 5pcd31 5qg006 5vd820 5v2g22 5v7b11 5vj921 5v0321 "
    "5w1h04 5w4f10 5wi522 5wac11 5x6e32 5z2b43 625321 62bf11 629h30 63i111 6gei43 6g2821 6g6522 "
    "6gf711 6gg933 6ghb21 6g1j20 6g3d11 6ha421 6hc000 6je811 6jf931 6j2710 6j5j11 6jih11 6k0b20 "
    "6k3400 6kc141 6lag31 6l6d43 6n7c24 6n1333 6n8510 6n9e01 6nb221 6nji12 6n4003 6oda22 6og622 "
    "6ohf31 6ueh40 6u3g20 6u5411 6ucb51 6ufj22 6u2d22 6va701 6vi902 6v0102 6w6860 71b315 71g514 "
    "71j002 728f20 721231 727i52 729a13 740650 75j201 757e20 758a20 75dg42 764c04 78ia22 789711 "
    "78d511 78eg51 78jb21 788310 781622 792430 79h023 79fc02 7d6h20 7eb811 7f0230 7f3900 7f5e14 "
    "7fgf13 7fcj51 7g4110 7g6i50 7gah42 7h7d40 7m9c04 7m2312 7m8g10 7me411 7mh521 7mib31 7mj713 "
    "7mf623 7nd001 7o1a33 7phc02 7q4612 7qde32 7u0821 7u3e24 7u4d02 7u5f12 7u6221 7u7150 7uaj20 "
    "7ub924 7uci31 7ugh03"
)


def season_2324() -> list[MatchRecord]:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = []
    for token in SEASON_2324.split():
        day = SEASON_2324_START + timedelta(days=int(token[:2], 36))
        h, a, hg, ag = (digits.index(c) for c in token[2:])
        out.append(record(day, TEAMS_2324[h], TEAMS_2324[a], hg, ag))
    return out


def test_real_season_fixture_is_complete() -> None:
    matches = season_2324()
    assert len(matches) == 380
    assert len({(m.home_team, m.away_team) for m in matches}) == 380
    assert sum(m.home_goals for m in matches) == 684  # 1.80 per match
    assert sum(m.away_goals for m in matches) == 562  # 1.48 per match


def test_home_advantage_is_positive_on_a_real_season() -> None:
    model = DixonColesModel()
    model.fit(season_2324(), date(2024, 5, 20))
    p = model.parameters
    assert p.converged
    assert 0.05 < p.home_adv < 0.4
    assert -0.3 < p.rho < 0.3
    # Sanity: the champions rate above the bottom side on both attack and defence.
    assert p.attack["Manchester City"] > p.attack["Sheffield United"]
    assert p.defence["Arsenal"] > p.defence["Sheffield United"]
    home, draw, away = model.forecast("Manchester City", "Sheffield United").outcome_probabilities()
    assert home > 0.7 and away < 0.1


# --- Through the backtest harness -----------------------------------------------------------


def fixture_records() -> list[MatchRecord]:
    return [
        *parse_season_csv((FIXTURES / "backtest" / "E0_1819.csv").read_bytes(), "1819"),
        *parse_season_csv((FIXTURES / "backtest" / "E0_1920.csv").read_bytes(), "1920"),
    ]


def test_runs_through_the_backtest_harness_on_the_fixtures() -> None:
    result = run_backtest(fixture_records(), DixonColesModel(), warmup_seasons=("1819",))
    assert result.model_name == "dixon_coles"
    assert result.overall.n_predicted == 4
    for prediction in result.predictions:
        assert sum(prediction.model) == pytest.approx(1.0, abs=1e-9)
        assert all(0 < p < 1 for p in prediction.model)

    # The two parser sample files as well (2016/17 warm-up, 2023/24 predicted).
    samples = [
        *parse_season_csv((FIXTURES / "E0_1617_sample.csv").read_bytes(), "1617"),
        *parse_season_csv((FIXTURES / "E0_2324_sample.csv").read_bytes(), "2324"),
    ]
    result = run_backtest(samples, DixonColesModel())
    assert result.overall.n_predicted == len([r for r in samples if r.season == "2324"])


def test_beats_trivial_in_a_walk_forward_backtest_on_a_simulated_league() -> None:
    rng = np.random.default_rng(21)
    n = 12
    attack = rng.normal(0, 0.35, n)
    defence = rng.normal(0, 0.35, n)
    first = simulate_league(
        attack, defence, 0.25, -0.1, rounds=2, start=date(2022, 8, 8), seed=8, season="2223"
    )
    second = simulate_league(
        attack, defence, 0.25, -0.1, rounds=1, start=date(2023, 8, 7), seed=9, season="2324"
    )
    dc = run_backtest(first + second, DixonColesModel(), warmup_seasons=("2223",))
    trivial = run_backtest(first + second, TrivialModel(), warmup_seasons=("2223",))
    assert dc.overall.model.rps < trivial.overall.model.rps


def test_never_reads_odds() -> None:
    history = season_2324()
    view = MatchView("2324", date(2024, 5, 25), None, "Arsenal", "Chelsea")
    priced = dataclasses.replace(
        view, odds=(OddsQuote("B365", "pre_match", 1.2, 7.0, 15.0, 1.5, 2.6),)
    )
    with_odds = [
        dataclasses.replace(r, odds=(OddsQuote("AVG", "pre_match", 9.0, 9.0, 1.1, None, None),))
        for r in history
    ]
    a, b = DixonColesModel(), DixonColesModel()
    a.fit(history, date(2024, 5, 20))
    b.fit(with_odds, date(2024, 5, 20))
    assert a.predict(view) == b.predict(priced)


def test_registered_in_the_cli() -> None:
    assert cli.MODELS["dixon_coles"] is DixonColesModel
    assert DixonColesModel().name == "dixon_coles"


def test_predict_before_fit_fails_clearly() -> None:
    with pytest.raises(RuntimeError, match="fit"):
        DixonColesModel().predict(MatchView("2324", date(2024, 1, 1), None, "A", "B"))
