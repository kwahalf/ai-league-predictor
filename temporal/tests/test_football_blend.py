"""Tests for the headline blend model (plan step 2.5).

- Leakage: the blend's weights are trained only on out-of-sample component predictions; results
  after a date D change nothing (weights or predictions) in windows starting before D.
- Before ``min_train`` out-of-sample rows the headline is the prior (the market consensus).
- Fallback: without usable odds the headline is the fundamentals 1X2, marked ``used_odds=False``.
- ``forecast`` returns a score matrix that agrees with the headline.
- It runs through the 2.2 harness on the existing fixtures and is registered in the CLI.

No test downloads anything.
"""

from __future__ import annotations

import dataclasses
import math
import random
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from src.football.backtest import MatchView, Model, cli, market_consensus, run_backtest
from src.football.backtest.inputs import history_record, match_view
from src.football.data import MatchRecord, OddsQuote, parse_season_csv
from src.football.models.blend import (
    ONE_X_TWO_TOLERANCE,
    BlendModel,
    market_over_2_5,
)
from src.football.models.dixon_coles import DixonColesModel, ScoreForecast

FIXTURES = Path(__file__).parent / "fixtures" / "football"


# --- A synthetic league with odds ------------------------------------------------------------


def _quote(source: str, probs: tuple[float, float, float], over: float, rng: random.Random):
    """Prices from noisy probabilities with a ~5% margin."""
    noisy = np.array(probs) * np.exp(rng.gauss(0, 0.08) * np.array([1, 0, -1]))
    noisy /= noisy.sum()
    o = min(max(over + rng.gauss(0, 0.02), 0.05), 0.95)
    return OddsQuote(
        source=source,  # type: ignore[arg-type]
        snapshot="pre_match",
        home=round(1 / (noisy[0] * 1.05), 2),
        draw=round(1 / (noisy[1] * 1.05), 2),
        away=round(1 / (noisy[2] * 1.05), 2),
        over_2_5=round(1 / (o * 1.05), 2),
        under_2_5=round(1 / ((1 - o) * 1.05), 2),
    )


def synthetic_league(seed: int = 3, n_seasons: int = 3) -> list[MatchRecord]:
    """``n_seasons`` double round robins of 8 teams, one round a week, with B365/AVG odds from
    the true probabilities (plus noise) and goals drawn from the true rates."""
    rng = random.Random(seed)
    nprng = np.random.default_rng(seed)
    teams = [f"T{i}" for i in range(8)]
    attack = {t: rng.gauss(0, 0.3) for t in teams}
    defence = {t: rng.gauss(0, 0.3) for t in teams}
    out: list[MatchRecord] = []
    for s in range(n_seasons):
        season = f"{16 + s}{17 + s}"
        start = date(2016 + s, 8, 13)  # a Saturday
        # Circle-method round robin, then the reverse fixtures.
        rot = teams[:]
        rounds = []
        for _ in range(len(teams) - 1):
            rounds.append([(rot[i], rot[-1 - i]) for i in range(len(teams) // 2)])
            rot = [rot[0], rot[-1], *rot[1:-1]]
        rounds += [[(a, h) for h, a in r] for r in rounds]
        for week, fixtures in enumerate(rounds):
            day = start + timedelta(days=7 * week)
            for h, a in fixtures:
                lam = math.exp(0.25 + attack[h] - defence[a])
                mu = math.exp(attack[a] - defence[h])
                fc = ScoreForecast.from_rates(lam, mu, 0.0)
                probs = fc.outcome_probabilities()
                over = fc.over_under()[0]
                hg, ag = int(nprng.poisson(lam)), int(nprng.poisson(mu))
                result = "H" if hg > ag else "A" if ag > hg else "D"
                odds = (_quote("B365", probs, over, rng), _quote("AVG", probs, over, rng))
                out.append(
                    MatchRecord(
                        season, day, None, h, a, hg, ag, result, odds=odds  # type: ignore[arg-type]
                    )
                )
    return out


def flip_after(records: list[MatchRecord], cutoff: date) -> list[MatchRecord]:
    """Swap the goals (and result) of every match on or after ``cutoff``."""
    flipped = {"H": "A", "A": "H", "D": "D"}
    return [
        dataclasses.replace(
            r, home_goals=r.away_goals, away_goals=r.home_goals, result=flipped[r.result]
        )
        if r.match_date >= cutoff and r.result is not None
        else r
        for r in records
    ]


class RecordingBlend(BlendModel):
    """Remembers the weights after every fit and the blend inputs of every prediction."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.weights_by_window: dict[date, object] = {}
        self.inputs_at_prediction: dict[tuple[str, str, str], tuple[float, ...]] = {}

    def fit(self, history, as_of) -> None:
        super().fit(history, as_of)
        self.weights_by_window[as_of] = self.weights

    def _components(self, match):
        c = super()._components(match)
        if c.z is not None:
            self.inputs_at_prediction[c.key] = c.z
        return c


def assert_valid(probs) -> None:
    assert len(probs) == 3
    assert all(0.0 <= p <= 1.0 for p in probs)
    assert math.isclose(sum(probs), 1.0, abs_tol=1e-9)


# --- Protocol and registration ---------------------------------------------------------------


def test_implements_model_protocol_and_is_registered() -> None:
    assert isinstance(BlendModel(), Model)
    assert BlendModel.name == "blend"
    assert cli.MODELS["blend"] is BlendModel


def test_rejects_bad_settings() -> None:
    with pytest.raises(ValueError):
        BlendModel(l2=0.0)
    with pytest.raises(ValueError):
        BlendModel(min_train=0)


def test_predict_before_fit_raises() -> None:
    view = match_view(synthetic_league(n_seasons=1)[0])
    with pytest.raises(RuntimeError):
        BlendModel().predict(view)


# --- Leakage -----------------------------------------------------------------------------------


def test_future_results_change_nothing_in_earlier_windows() -> None:
    """The leakage test: flip every result from date D on; the weights after each fit and every
    prediction of windows starting before D must be unchanged."""
    records = synthetic_league()
    base_model = RecordingBlend(min_train=40)
    base = run_backtest(records, base_model, warmup_seasons=("1617",))
    windows = sorted({p.window_start for p in base.predictions})
    cutoff = windows[len(windows) * 2 // 3]  # a window in the last season
    # The test is only meaningful if the blend was already trained before the cutoff.
    trained_before = [w for w in windows if w < cutoff and base_model.weights_by_window[w].n_train]
    assert trained_before, "blend never trained before the cutoff: test would be vacuous"

    other_model = RecordingBlend(min_train=40)
    other = run_backtest(flip_after(records, cutoff), other_model, warmup_seasons=("1617",))

    for w in windows:
        if w < cutoff:
            assert other_model.weights_by_window[w] == base_model.weights_by_window[w], w
    pairs = list(zip(base.predictions, other.predictions))
    before = [(p.model, q.model) for p, q in pairs if p.window_start < cutoff]
    assert before and all(a == b for a, b in before)
    # The flip does matter once its results are visible (sanity check of the test itself).
    after = [(p.model, q.model) for p, q in pairs if p.window_start > cutoff]
    assert any(a != b for a, b in after)


def test_training_rows_are_the_out_of_sample_predictions() -> None:
    """Every training row is exactly what the components said when the match was predicted,
    before its result was in any history (never a refitted, in-sample prediction)."""
    records = synthetic_league()
    model = RecordingBlend(min_train=40)
    run_backtest(records, model, warmup_seasons=("1617",))
    played = sorted(
        (r for r in records if r.season != "1617" and r.match_date < model.as_of),
        key=lambda r: r.match_date,
    )
    expected = [model.inputs_at_prediction[(r.season, r.home_team, r.away_team)] for r in played]
    assert model.n_training_rows == len(played)
    # Training rows are added in history order; compare as multisets keyed by content.
    assert sorted(model._train_x) == sorted(expected)


def test_predicting_a_past_match_is_not_recorded_for_training() -> None:
    records = synthetic_league(n_seasons=2)
    model = BlendModel(min_train=1)
    as_of = records[-1].match_date
    history = [history_record(r) for r in records if r.match_date < as_of]
    model.fit(history, as_of)
    model.predict(match_view(records[0]))  # played long before as_of: components saw it
    rest = [history_record(r) for r in records if r.match_date >= as_of]
    model.fit(history + rest, as_of + timedelta(days=7))
    assert model.n_training_rows == 0


def test_a_history_that_does_not_continue_resets_recorded_state() -> None:
    records = synthetic_league()
    model = BlendModel(min_train=40)
    run_backtest(records, model, warmup_seasons=("1617",))
    assert model.weights_trained
    first = records[0].match_date + timedelta(days=60)
    model.fit([history_record(r) for r in records if r.match_date < first], first)
    assert not model.weights_trained
    assert model.n_training_rows == 0


# --- Prior, fallback, forecast -----------------------------------------------------------------


def test_headline_is_the_consensus_until_enough_out_of_sample_rows() -> None:
    records = synthetic_league(n_seasons=2)
    result = run_backtest(records, BlendModel(), warmup_seasons=("1617",))  # min_train 380
    for p in result.predictions:
        assert p.market is not None
        assert p.model == pytest.approx(p.market, abs=1e-12)


def test_trained_blend_differs_from_the_consensus() -> None:
    model = BlendModel(min_train=40)
    result = run_backtest(synthetic_league(), model, warmup_seasons=("1617",))
    assert model.weights_trained and model.weights.n_train >= 40
    last = [p for p in result.predictions if p.season == "1819"]
    assert any(abs(p.model[0] - p.market[0]) > 1e-3 for p in last)  # type: ignore[index]
    for p in result.predictions:
        assert_valid(p.model)


def _fitted_blend() -> tuple[BlendModel, list[MatchRecord]]:
    records = synthetic_league()
    model = BlendModel(min_train=40)
    run_backtest(records, model, warmup_seasons=("1617",))
    return model, records


def test_no_odds_falls_back_to_fundamentals() -> None:
    model, records = _fitted_blend()
    rec = records[-1]
    view = dataclasses.replace(match_view(rec), odds=())
    fc = model.forecast(view)
    assert fc.used_odds is False
    assert fc.market is None and fc.solved is None
    assert fc.headline == fc.fundamentals == model.fundamentals.predict(view)
    assert model.predict(view) == fc.fundamentals
    dc = model.fundamentals.forecast(rec.home_team, rec.away_team, rec.season)
    assert fc.scores.expected_goals == dc.expected_goals
    np.testing.assert_allclose(fc.scores.matrix, dc.matrix)


def test_unusable_odds_also_fall_back() -> None:
    model, records = _fitted_blend()
    bad = OddsQuote("B365", "pre_match", None, 3.4, 2.1, 1.9, 1.9)  # no home price
    view = dataclasses.replace(match_view(records[-1]), odds=(bad,))
    fc = model.forecast(view)
    assert fc.used_odds is False and fc.headline == fc.fundamentals


def test_forecast_with_odds_agrees_with_headline() -> None:
    model, records = _fitted_blend()
    for rec in records[-8:]:
        view = match_view(rec)
        fc = model.forecast(view)
        assert fc.used_odds is True and fc.weights_trained
        assert fc.headline == model.predict(view)
        assert fc.market == market_consensus(view.odds)
        assert fc.solved is not None
        got = fc.scores.outcome_probabilities()
        assert max(abs(a - b) for a, b in zip(got, fc.headline)) <= ONE_X_TWO_TOLERANCE
        assert fc.market_over_2_5 is not None
        assert fc.solved.target_over_2_5 == fc.market_over_2_5
        assert fc.scores.rho == model.fundamentals.parameters.rho
        assert_valid(fc.elo)
        assert_valid(fc.fundamentals)


def test_weights_serialise() -> None:
    model, _ = _fitted_blend()
    d = model.weights.as_dict()
    assert d["n_train"] == model.weights.n_train
    assert len(d["coefficients"]) == 3 and all(len(row) == 7 for row in d["coefficients"])


# --- Over/under consensus ----------------------------------------------------------------------


def test_market_over_2_5_is_the_margin_free_average() -> None:
    quotes = (
        OddsQuote("B365", "pre_match", 2.0, 3.4, 3.8, 1.8, 2.0),
        OddsQuote("BW", "pre_match", 2.0, 3.4, 3.8, None, None),
        OddsQuote("AVG", "pre_match", 2.0, 3.4, 3.8, 2.0, 1.85),
        OddsQuote("PS", "pre_match", 2.0, 3.4, 3.8, 1.2, 4.0),  # benchmark only: ignored
        OddsQuote("B365", "closing", 2.0, 3.4, 3.8, 1.2, 4.0),  # closing: ignored
    )
    b365 = (1 / 1.8) / (1 / 1.8 + 1 / 2.0)
    avg = (1 / 2.0) / (1 / 2.0 + 1 / 1.85)
    assert market_over_2_5(quotes) == pytest.approx((b365 + avg) / 2)
    assert market_over_2_5(quotes[1:2]) is None
    assert market_over_2_5(()) is None


# --- Harness on the existing fixtures ----------------------------------------------------------


def test_backtest_on_fixture_files() -> None:
    records = [
        *parse_season_csv((FIXTURES / "backtest" / "E0_1819.csv").read_bytes(), "1819"),
        *parse_season_csv((FIXTURES / "backtest" / "E0_1920.csv").read_bytes(), "1920"),
    ]
    result = run_backtest(records, BlendModel(), warmup_seasons=("1819",))
    assert result.model_name == "blend"
    assert len(result.predictions) == 4
    for p in result.predictions:
        assert_valid(p.model)
    assert result.overall.model is not None


def test_backtest_on_sample_files_matches_market_or_fundamentals() -> None:
    samples = [
        *parse_season_csv((FIXTURES / "E0_1617_sample.csv").read_bytes(), "1617"),
        *parse_season_csv((FIXTURES / "E0_2324_sample.csv").read_bytes(), "2324"),
    ]
    result = run_backtest(samples, BlendModel())
    assert result.predictions
    dc = run_backtest(samples, DixonColesModel())
    for p, q in zip(result.predictions, dc.predictions):
        assert_valid(p.model)
        # Untrained: the consensus where there are odds, the fundamentals where there aren't.
        assert p.model == pytest.approx(p.market if p.market is not None else q.model, abs=1e-12)


def test_view_has_no_outcome() -> None:
    # The blend only ever sees MatchView for predictions; nothing to read a result from.
    assert not {"home_goals", "away_goals", "result"} & {
        f.name for f in dataclasses.fields(MatchView)
    }
