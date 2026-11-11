"""Tests for the blend's penalised multinomial logit (plan step 2.5).

- The prior (no data, or an overwhelming penalty) is the market consensus exactly.
- Regularisation: a stronger L2 penalty keeps the weights closer to the prior, monotonically; a
  weak one recovers known weights on synthetic data.
- The analytic gradient matches finite differences.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.football.models.blend import (
    FEATURES,
    PROB_FLOOR,
    BlendWeights,
    features,
    fit_blend,
    prior_coefficients,
)
from src.football.models.blend.logit import penalised_nll


def softmax(s: np.ndarray) -> np.ndarray:
    e = np.exp(s - s.max(axis=-1, keepdims=True))
    return e / e.sum(axis=-1, keepdims=True)


def synthetic(n: int, seed: int = 0, market_w: float = 0.7, fund_w: float = 0.3):
    """Fundamentals and market 1X2 vectors and outcomes drawn from a known log-linear pool."""
    rng = np.random.default_rng(seed)
    strength = rng.normal(0.3, 0.8, size=n)
    base = np.stack([strength, np.full(n, 0.0), -strength], axis=1)
    fund = softmax(base + rng.normal(0, 0.35, size=(n, 3)))
    mkt = softmax(base + rng.normal(0, 0.35, size=(n, 3)))
    elo = strength / 2 + rng.normal(0, 0.1, size=n)
    x = np.array([features(f, m, e) for f, m, e in zip(fund, mkt, elo)])
    true = softmax(fund_w * np.log(fund) + market_w * np.log(mkt))
    y = np.array([rng.choice(3, p=p) for p in true])
    return x, y, fund, mkt


def test_features_order_and_floor() -> None:
    z = features((0.5, 0.3, 0.2), (0.6, 0.25, 0.0), 0.4)
    assert len(z) == len(FEATURES) == 7
    assert z[0] == pytest.approx(np.log(0.5))
    assert z[5] == pytest.approx(np.log(PROB_FLOOR))
    assert z[6] == 0.4


def test_prior_is_the_market_consensus() -> None:
    prior = BlendWeights.prior(prior_coefficients(), 10.0)
    for fund, mkt in [((0.5, 0.3, 0.2), (0.45, 0.28, 0.27)), ((0.1, 0.2, 0.7), (0.2, 0.25, 0.55))]:
        assert prior.probabilities(features(fund, mkt, 0.3)) == pytest.approx(mkt, abs=1e-12)


def test_overwhelming_penalty_returns_the_prior() -> None:
    x, y, _, mkt = synthetic(2000)
    w = fit_blend(x, y, l2=1e9)
    _, coef = w.arrays()
    np.testing.assert_allclose(coef, prior_coefficients(), atol=1e-4)
    # The intercepts are free, so the forecast is the consensus up to a base-rate shift.
    assert np.abs(np.array(w.probabilities(x[0])) - mkt[0]).max() < 0.05


def test_stronger_penalty_stays_closer_to_the_prior() -> None:
    x, y, _, _ = synthetic(1500, seed=1)
    w0 = prior_coefficients()
    distances = []
    for l2 in (0.01, 1.0, 10.0, 100.0, 1000.0):
        _, coef = fit_blend(x, y, l2=l2).arrays()
        distances.append(float(np.linalg.norm(coef - w0)))
    assert all(a > b for a, b in zip(distances, distances[1:])), distances


def test_weak_penalty_recovers_the_pool_weights() -> None:
    x, y, fund, mkt = synthetic(30000, seed=2, market_w=0.7, fund_w=0.3)
    w = fit_blend(x, y, l2=1.0)
    p_fit = np.array([w.probabilities(z) for z in x[:2000]])
    p_true = softmax(0.3 * np.log(fund[:2000]) + 0.7 * np.log(mkt[:2000]))
    assert np.abs(p_fit - p_true).mean() < 0.01


def test_prior_can_be_centred_elsewhere() -> None:
    x, y, _, _ = synthetic(500, seed=3)
    w0 = prior_coefficients(market_weight=0.5, fundamentals_weight=0.5)
    _, coef = fit_blend(x, y, l2=1e9, w0=w0).arrays()
    np.testing.assert_allclose(coef, w0, atol=1e-4)


def test_warm_start_gives_the_same_fit() -> None:
    x, y, _, _ = synthetic(1000, seed=4)
    cold = fit_blend(x, y, l2=10.0)
    warm = fit_blend(x, y, l2=10.0, start=fit_blend(x[:500], y[:500], l2=10.0))
    np.testing.assert_allclose(
        np.array(warm.coefficients), np.array(cold.coefficients), atol=1e-3
    )


def test_gradient_matches_finite_differences() -> None:
    x, y, _, _ = synthetic(200, seed=5)
    rng = np.random.default_rng(0)
    theta = rng.normal(0, 0.5, size=2 + 21)
    w0 = prior_coefficients()
    _, grad = penalised_nll(theta, x, y, w0, 3.0)
    eps = 1e-6
    for i in range(len(theta)):
        up, down = theta.copy(), theta.copy()
        up[i] += eps
        down[i] -= eps
        numeric = (penalised_nll(up, x, y, w0, 3.0)[0] - penalised_nll(down, x, y, w0, 3.0)[0]) / (
            2 * eps
        )
        assert grad[i] == pytest.approx(numeric, rel=1e-4, abs=1e-4)


def test_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        fit_blend(np.zeros((3, 6)), np.zeros(3, dtype=int))
    with pytest.raises(ValueError):
        fit_blend(np.zeros((0, 7)), np.zeros(0, dtype=int))
    with pytest.raises(ValueError):
        fit_blend(np.zeros((3, 7)), np.zeros(3, dtype=int), l2=-1.0)


@pytest.mark.parametrize(
    "labels",
    [
        np.array([0, 1, -1]),  # -1 would index the away column
        np.array([0, 1, 3]),
        np.array([0.0, 1.5, 2.0]),  # would be truncated to 1
        np.array([[0], [1], [2]]),  # (n, 1) would broadcast the likelihood lookup
    ],
)
def test_rejects_bad_outcome_labels(labels) -> None:
    with pytest.raises(ValueError):
        fit_blend(np.zeros((3, 7)), labels)


def test_accepts_integral_float_labels() -> None:
    x, y, _, _ = synthetic(300, seed=6)
    a = fit_blend(x, y.astype(float), l2=10.0)
    b = fit_blend(x, y, l2=10.0)
    assert a == b


# --- Step 2.6: the "pool" structure and sample weights -----------------------------------------


def test_pool_gradient_matches_finite_differences() -> None:
    from src.football.models.blend.logit import POOL_BASIS

    x, y, _, _ = synthetic(200, seed=6)
    rng = np.random.default_rng(1)
    theta = rng.normal(0, 0.5, size=2 + 3)
    sw = rng.uniform(0.2, 1.0, size=len(y))
    c0 = np.array([1.0, 0.0, 0.0])
    _, grad = penalised_nll(theta, x, y, c0, 3.0, sw, POOL_BASIS)
    eps = 1e-6
    for i in range(len(theta)):
        up, down = theta.copy(), theta.copy()
        up[i] += eps
        down[i] -= eps
        numeric = (
            penalised_nll(up, x, y, c0, 3.0, sw, POOL_BASIS)[0]
            - penalised_nll(down, x, y, c0, 3.0, sw, POOL_BASIS)[0]
        ) / (2 * eps)
        assert grad[i] == pytest.approx(numeric, rel=1e-4, abs=1e-4)


def test_pool_recovers_the_pool_weights_and_has_its_shape() -> None:
    x, y, _, _ = synthetic(6000, seed=8)  # truth: market 0.7, fundamentals 0.3, no Elo
    w = fit_blend(x, y, l2=1.0, structure="pool")
    assert w.structure == "pool"
    coef = np.array(w.coefficients)
    for k in range(3):
        assert coef[k, 3 + k] == pytest.approx(coef[0, 3], abs=1e-12)  # one market weight
        assert coef[k, k] == pytest.approx(coef[0, 0], abs=1e-12)  # one fundamentals weight
    assert coef[0, 3] == pytest.approx(0.7, abs=0.15)
    assert coef[0, 0] == pytest.approx(0.3, abs=0.15)
    assert coef[1, 6] == 0.0 and coef[0, 6] == pytest.approx(-coef[2, 6])
    # Off-pattern cells stay exactly zero.
    assert coef[0, 4] == coef[0, 1] == 0.0


def test_pool_with_overwhelming_penalty_is_the_prior() -> None:
    x, y, _, _ = synthetic(1000, seed=9)
    _, coef = fit_blend(x, y, l2=1e9, structure="pool").arrays()
    np.testing.assert_allclose(coef, prior_coefficients(), atol=1e-5)


def test_integer_sample_weights_equal_repeated_rows() -> None:
    x, y, _, _ = synthetic(300, seed=10)
    counts = np.random.default_rng(2).integers(1, 4, size=len(y))
    weighted = fit_blend(x, y, l2=5.0, sample_weight=counts.astype(float))
    repeated = fit_blend(np.repeat(x, counts, axis=0), np.repeat(y, counts), l2=5.0)
    np.testing.assert_allclose(
        np.array(weighted.coefficients), np.array(repeated.coefficients), atol=1e-4
    )
    np.testing.assert_allclose(weighted.intercepts, repeated.intercepts, atol=1e-4)


def test_rejects_bad_structure_and_weights() -> None:
    x, y, _, _ = synthetic(50, seed=11)
    with pytest.raises(ValueError):
        fit_blend(x, y, structure="diagonal")
    with pytest.raises(ValueError):
        fit_blend(x, y, sample_weight=np.full(len(y), -1.0))
    with pytest.raises(ValueError):
        fit_blend(x, y, sample_weight=np.ones(len(y) - 1))


def test_serialised_weights_name_the_structure() -> None:
    x, y, _, _ = synthetic(300, seed=12)
    d = fit_blend(x, y, structure="pool", train_half_life_days=365.0).as_dict()
    assert d["structure"] == "pool" and d["train_half_life_days"] == 365.0
