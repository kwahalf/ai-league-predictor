"""Metrics library (step 2.1a): every function against hand-computed examples.

Outcomes are 1X2 ordered (home, draw, away) = (0, 1, 2).
"""

import math

import pytest

from src.football.metrics import (
    AWAY,
    DRAW,
    HOME,
    LOG_LOSS_EPSILON,
    CalibrationBucket,
    MetricsError,
    brier,
    bucket_index,
    calibration_buckets,
    consensus,
    log_loss,
    mean_brier,
    mean_log_loss,
    mean_rps,
    remove_margin,
    rps,
)

FORECAST = (0.5, 0.3, 0.2)  # cumulative: 0.5, 0.8


# --- RPS -------------------------------------------------------------------------------------


def test_rps_home_win():
    # observed cumulative (1, 1): ((0.5-1)^2 + (0.8-1)^2) / 2 = (0.25 + 0.04) / 2 = 0.145
    assert rps(FORECAST, HOME) == pytest.approx(0.145)


def test_rps_draw():
    # observed cumulative (0, 1): ((0.5-0)^2 + (0.8-1)^2) / 2 = (0.25 + 0.04) / 2 = 0.145
    assert rps(FORECAST, DRAW) == pytest.approx(0.145)


def test_rps_away_win():
    # observed cumulative (0, 0): ((0.5-0)^2 + (0.8-0)^2) / 2 = (0.25 + 0.64) / 2 = 0.445
    assert rps(FORECAST, AWAY) == pytest.approx(0.445)


def test_rps_uniform_forecast():
    # cumulative (1/3, 2/3) vs (1, 1): ((2/3)^2 + (1/3)^2) / 2 = (4/9 + 1/9) / 2 = 5/18
    assert rps((1 / 3, 1 / 3, 1 / 3), HOME) == pytest.approx(5 / 18)


def test_rps_perfect_and_worst():
    assert rps((1.0, 0.0, 0.0), HOME) == 0.0
    assert rps((0.0, 0.0, 1.0), AWAY) == 0.0
    # cumulative (0, 0) vs (1, 1): (1 + 1) / 2 = 1, the maximum
    assert rps((0.0, 0.0, 1.0), HOME) == pytest.approx(1.0)


def test_rps_respects_order():
    # Away win. A certain draw: cumulative (0, 1) vs (0, 0) -> (0 + 1) / 2 = 0.5.
    # A certain home win: cumulative (1, 1) vs (0, 0) -> (1 + 1) / 2 = 1. The draw is "less wrong".
    assert rps((0.0, 1.0, 0.0), AWAY) == pytest.approx(0.5)
    assert rps((1.0, 0.0, 0.0), AWAY) == pytest.approx(1.0)


def test_rps_two_way():
    # r = 2, so no normalisation: (0.7 - 0)^2 / 1 = 0.49 when the second outcome happens
    assert rps((0.7, 0.3), 1) == pytest.approx(0.49)


# --- Log loss --------------------------------------------------------------------------------


def test_log_loss():
    assert log_loss(FORECAST, HOME) == pytest.approx(math.log(2))  # -ln 0.5 = 0.693147
    assert log_loss(FORECAST, DRAW) == pytest.approx(-math.log(0.3))  # 1.203973
    assert log_loss(FORECAST, AWAY) == pytest.approx(math.log(5))  # -ln 0.2 = 1.609438


def test_log_loss_perfect_and_worst_is_clipped():
    assert log_loss((1.0, 0.0, 0.0), HOME) == 0.0
    # p = 0 is clipped to 1e-15: -ln(1e-15) = 15 ln 10 = 34.538776, not infinity
    worst = log_loss((0.0, 0.0, 1.0), HOME)
    assert LOG_LOSS_EPSILON == 1e-15
    assert worst == pytest.approx(15 * math.log(10))
    assert worst == pytest.approx(34.538776)


# --- Brier -----------------------------------------------------------------------------------


def test_brier():
    # home: (0.5-1)^2 + 0.3^2 + 0.2^2 = 0.25 + 0.09 + 0.04 = 0.38
    assert brier(FORECAST, HOME) == pytest.approx(0.38)
    # away: 0.5^2 + 0.3^2 + (0.2-1)^2 = 0.25 + 0.09 + 0.64 = 0.98
    assert brier(FORECAST, AWAY) == pytest.approx(0.98)


def test_brier_perfect_and_worst():
    assert brier((1.0, 0.0, 0.0), HOME) == 0.0
    # (0-1)^2 + 0 + (1-0)^2 = 2, the maximum of the summed convention
    assert brier((0.0, 0.0, 1.0), HOME) == pytest.approx(2.0)


# --- Aggregates ------------------------------------------------------------------------------


def test_means():
    predictions = [(FORECAST, HOME), (FORECAST, AWAY)]
    assert mean_rps(predictions) == pytest.approx((0.145 + 0.445) / 2)  # 0.295
    assert mean_brier(predictions) == pytest.approx((0.38 + 0.98) / 2)  # 0.68
    # (-ln 0.5 - ln 0.2) / 2 = (ln 2 + ln 5) / 2 = ln 10 / 2 = 1.151293
    assert mean_log_loss(predictions) == pytest.approx(math.log(10) / 2)


def test_means_accept_a_generator():
    assert mean_rps((FORECAST, o) for o in (HOME, DRAW)) == pytest.approx(0.145)


@pytest.mark.parametrize("mean", [mean_rps, mean_log_loss, mean_brier])
def test_means_reject_empty(mean):
    with pytest.raises(MetricsError, match="at least one"):
        mean([])


# --- Calibration -----------------------------------------------------------------------------


def test_calibration_buckets():
    predictions = [
        ((0.5, 0.3, 0.2), HOME),
        ((0.55, 0.25, 0.2), AWAY),
        ((0.05, 0.15, 0.8), AWAY),
        ((1.0, 0.0, 0.0), HOME),
    ]
    buckets = calibration_buckets(predictions)
    assert len(buckets) == 10
    assert [(b.lower, b.upper) for b in buckets][0] == (0.0, 0.1)
    assert (buckets[9].lower, buckets[9].upper) == (0.9, 1.0)
    # 4 predictions x 3 outcomes = 12 forecasts
    assert sum(b.count for b in buckets) == 12

    def check(b, count, mean_predicted, observed):
        assert b.count == count
        assert b.mean_predicted == pytest.approx(mean_predicted)
        assert b.observed_frequency == pytest.approx(observed)

    # [0.0, 0.1): 0.05 (no), 0.0 (no), 0.0 (no) -> mean 0.05/3, observed 0/3
    check(buckets[0], 3, 0.05 / 3, 0.0)
    # [0.1, 0.2): 0.15 (no)
    check(buckets[1], 1, 0.15, 0.0)
    # [0.2, 0.3): 0.2 (no), 0.25 (no), 0.2 (yes) -> mean 0.65/3 = 0.216667, observed 1/3
    check(buckets[2], 3, 0.65 / 3, 1 / 3)
    # [0.3, 0.4): 0.3 (no): the lower edge belongs to the bucket
    check(buckets[3], 1, 0.3, 0.0)
    # [0.5, 0.6): 0.5 (yes), 0.55 (no) -> mean 0.525, observed 0.5
    check(buckets[5], 2, 0.525, 0.5)
    # [0.8, 0.9): 0.8 (yes)
    check(buckets[8], 1, 0.8, 1.0)
    # [0.9, 1.0]: 1.0 (yes): the top edge goes in the last bucket
    check(buckets[9], 1, 1.0, 1.0)
    for b in (buckets[4], buckets[6], buckets[7]):
        assert b == CalibrationBucket(b.lower, b.upper, 0, None, None)


@pytest.mark.parametrize("k", range(11))
def test_bucket_edges_are_lower_inclusive(k):
    # k/10 lands in bucket k (and 1.0 in bucket 9), despite float rounding
    assert bucket_index(k / 10) == min(k, 9)


def test_calibration_rejects_bad_bucket_count():
    with pytest.raises(MetricsError, match="n_buckets"):
        calibration_buckets([], n_buckets=0)


def test_calibration_validates_predictions():
    with pytest.raises(MetricsError, match="sum to 1"):
        calibration_buckets([((0.5, 0.5, 0.5), HOME)])


# --- Margin removal --------------------------------------------------------------------------


def test_remove_margin_1x2():
    # 2.10 / 3.40 / 3.60 -> 1/odds = 10/21, 5/17, 5/18 = 1020/2142, 630/2142, 595/2142
    # sum = 2245/2142 = 1.048086 -> margin = 103/2142 = 0.048086 (4.8%)
    # p = 1020/2245, 630/2245, 595/2245 = 0.454343, 0.280624, 0.265033
    result = remove_margin((2.10, 3.40, 3.60))
    assert result.margin == pytest.approx(103 / 2142)
    assert result.margin == pytest.approx(0.048086, abs=1e-6)
    assert result.probabilities == pytest.approx((1020 / 2245, 630 / 2245, 595 / 2245))
    assert result.probabilities == pytest.approx((0.454343, 0.280624, 0.265033), abs=1e-6)
    assert math.fsum(result.probabilities) == pytest.approx(1.0)


def test_remove_margin_over_under():
    # 1.90 / 1.90 -> 1/1.9 + 1/1.9 = 20/19 -> margin 1/19 = 0.052632; p = 0.5, 0.5
    result = remove_margin((1.90, 1.90))
    assert result.margin == pytest.approx(1 / 19)
    assert result.probabilities == pytest.approx((0.5, 0.5))


def test_remove_margin_fair_book():
    # 2.0 / 2.0 -> 0.5 + 0.5 = 1: no margin
    result = remove_margin((2.0, 2.0))
    assert result.margin == pytest.approx(0.0)
    assert result.probabilities == pytest.approx((0.5, 0.5))


@pytest.mark.parametrize(
    "odds",
    [(1.0, 3.0, 4.0), (0.5, 3.0, 4.0), (-2.0, 3.0, 4.0), (float("nan"), 3.0, 4.0), (math.inf, 2.0)],
)
def test_remove_margin_rejects_bad_odds(odds):
    with pytest.raises(MetricsError, match="> 1.0"):
        remove_margin(odds)


def test_remove_margin_rejects_too_few_or_non_numeric():
    with pytest.raises(MetricsError, match="at least 2"):
        remove_margin((2.0,))
    with pytest.raises(MetricsError, match="numbers"):
        remove_margin(("abc", 2.0))


# --- Consensus -------------------------------------------------------------------------------


def test_consensus_equal_weights():
    # ((0.5+0.4)/2, (0.3+0.3)/2, (0.2+0.3)/2) = (0.45, 0.3, 0.25)
    assert consensus([(0.5, 0.3, 0.2), (0.4, 0.3, 0.3)]) == pytest.approx((0.45, 0.3, 0.25))


def test_consensus_ignores_missing_source():
    # The middle source has no odds; the average is over the other two only.
    result = consensus([(0.5, 0.3, 0.2), None, (0.4, 0.3, 0.3)])
    assert result == pytest.approx((0.45, 0.3, 0.25))


def test_consensus_single_source_is_that_source():
    assert consensus([None, (0.4, 0.3, 0.3), None]) == pytest.approx((0.4, 0.3, 0.3))


def test_consensus_weights_renormalised_over_present_sources():
    # weights (3, 5, 1) with the second source missing -> (3, 1) / 4
    # home: (0.5*3 + 0.4*1)/4 = 0.475; draw: 0.3; away: (0.2*3 + 0.3*1)/4 = 0.225
    result = consensus([(0.5, 0.3, 0.2), None, (0.4, 0.3, 0.3)], weights=(3, 5, 1))
    assert result == pytest.approx((0.475, 0.3, 0.225))


def test_consensus_of_real_odds():
    # Bet365 2.10/3.40/3.60 -> (0.454343, 0.280624, 0.265033); Bwin missing; market average
    # 2.0/3.5/4.0 -> 1/odds 0.5, 0.285714, 0.25, sum 1.035714 -> (0.482759, 0.275862, 0.241379)
    # consensus = ((0.454343+0.482759)/2, ...) = (0.468551, 0.278243, 0.253206)
    b365 = remove_margin((2.10, 3.40, 3.60)).probabilities
    avg = remove_margin((2.0, 3.5, 4.0)).probabilities
    assert avg == pytest.approx((14 / 29, 8 / 29, 7 / 29))
    result = consensus([b365, None, avg])
    assert result == pytest.approx((0.468551, 0.278243, 0.253206), abs=1e-6)
    assert math.fsum(result) == pytest.approx(1.0)


def test_consensus_errors():
    with pytest.raises(MetricsError, match="no source"):
        consensus([None, None, None])
    with pytest.raises(MetricsError, match="no source"):
        consensus([])
    with pytest.raises(MetricsError, match="same number of outcomes"):
        consensus([(0.5, 0.3, 0.2), (0.5, 0.5)])
    with pytest.raises(MetricsError, match="one entry per source"):
        consensus([(0.5, 0.3, 0.2)], weights=(1, 1))
    with pytest.raises(MetricsError, match="non-negative"):
        consensus([(0.5, 0.3, 0.2), (0.4, 0.3, 0.3)], weights=(1, -1))
    with pytest.raises(MetricsError, match="all zero"):
        consensus([(0.5, 0.3, 0.2), None], weights=(0, 1))
    with pytest.raises(MetricsError, match=r"sources\[1\]"):
        consensus([(0.5, 0.3, 0.2), (0.9, 0.3, 0.3)])


# --- Input validation ------------------------------------------------------------------------


@pytest.mark.parametrize("score", [rps, log_loss, brier])
@pytest.mark.parametrize(
    "probs, match",
    [
        ((0.5, 0.3, 0.3), "sum to 1"),
        ((0.5, 0.3), "sum to 1"),
        ((1.2, -0.1, -0.1), r"\[0, 1\]"),
        ((float("nan"), 0.5, 0.5), r"\[0, 1\]"),
        ((1.0,), "at least 2"),
        (("a", "b", "c"), "numbers"),
    ],
)
def test_scores_reject_bad_probs(score, probs, match):
    with pytest.raises(MetricsError, match=match):
        score(probs, HOME)


@pytest.mark.parametrize("score", [rps, log_loss, brier])
@pytest.mark.parametrize("outcome", [3, -1, 1.0, True, "H", None])
def test_scores_reject_bad_outcome(score, outcome):
    with pytest.raises(MetricsError, match="outcome"):
        score(FORECAST, outcome)


def test_tolerates_rounded_probabilities():
    # Rounded to 6 dp, (1/3, 1/3, 1/3) sums to 0.999999: inside the 1e-5 tolerance
    assert rps((0.333333, 0.333333, 0.333333), HOME) == pytest.approx(5 / 18, abs=1e-6)


def test_metrics_error_is_a_value_error():
    assert issubclass(MetricsError, ValueError)
