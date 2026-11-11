"""The headline blend's multinomial logistic regression (spec, "3. Headline model (with odds)").

**Inputs** (``FEATURES``), per match:

- the log-probabilities of the fundamentals (Dixon–Coles) 1X2: ``log f_home``, ``log f_draw``,
  ``log f_away``;
- the log of the margin-free pre-match consensus 1X2 (``market_consensus``): ``log m_home``,
  ``log m_draw``, ``log m_away``;
- the Elo difference ``x = (R_home + H − R_away) / 400`` (``elo.elo_difference``).

Probabilities are floored at ``PROB_FLOOR`` before the log so a near-certain price can't produce
an infinite input.

**Model.** For outcome ``k`` ∈ (home, draw, away), ``s_k = b_k + Σ_j W[k, j] · z_j`` and
``P(k) = exp(s_k) / Σ exp(s)``: a full multinomial logit, every outcome with its own coefficient on
every input (3 × 7 coefficients). ``b_away`` is fixed at 0 (only differences of intercepts
matter); the coefficients need no such constraint because the penalty makes them identifiable.

**Regularisation (L2, towards a prior).** Fitting minimises

    −Σ_i log P(y_i | z_i)  +  (l2 / 2) · ‖W − W₀‖²

over the training matches (a sum, not a mean, so the penalty is a fixed prior that the data
outweigh as they accumulate). The intercepts are not penalised. ``W₀`` (``prior_coefficients``)
is a log-linear pool: ``W₀[k, log m_k] = market_weight``, ``W₀[k, log f_k] = fundamentals_weight``,
everything else 0. With the defaults (market 1, fundamentals 0) the prior *is* the market
consensus: an infinitely strong penalty, or no training data at all, gives back the consensus
exactly, and the fit moves away from it only as far as the out-of-sample record supports. This is
an a-priori choice, not a result of tuning: betting markets are the strongest public forecasters
of football results, and the headline's target is to at least match the consensus. Centring the
penalty on zero instead would shrink towards a constant forecast (uniform up to the intercepts),
a far worse forecaster than either input. ``DEFAULT_L2`` = 10 is roughly a season's worth of
evidence on the fundamentals-versus-market contrast (that contrast varies little between matches,
so it is the least identified direction); step 2.6 tunes it walk-forward.

The objective is convex; it is minimised with L-BFGS using the analytic gradient.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np
from scipy.optimize import minimize

FEATURES: tuple[str, ...] = (
    "log_f_home", "log_f_draw", "log_f_away",
    "log_m_home", "log_m_draw", "log_m_away",
    "elo_diff",
)  # fmt: skip
"""The blend's inputs, in column order."""

OUTCOMES: tuple[str, ...] = ("home", "draw", "away")
PROB_FLOOR = 1e-4
DEFAULT_L2 = 10.0
DEFAULT_MARKET_WEIGHT = 1.0
DEFAULT_FUNDAMENTALS_WEIGHT = 0.0

_N_OUT = len(OUTCOMES)
_N_FEAT = len(FEATURES)


def features(
    fundamentals: Sequence[float], market: Sequence[float], elo_diff: float
) -> tuple[float, ...]:
    """The input vector ``z`` for one match (see ``FEATURES``)."""
    logs = [math.log(max(float(p), PROB_FLOOR)) for p in (*fundamentals, *market)]
    return (*logs, float(elo_diff))


def prior_coefficients(
    market_weight: float = DEFAULT_MARKET_WEIGHT,
    fundamentals_weight: float = DEFAULT_FUNDAMENTALS_WEIGHT,
) -> np.ndarray:
    """``W₀``: the log-linear pool the penalty shrinks towards (3 × 7)."""
    w0 = np.zeros((_N_OUT, _N_FEAT))
    for k in range(_N_OUT):
        w0[k, k] = fundamentals_weight
        w0[k, 3 + k] = market_weight
    return w0


@dataclass(frozen=True)
class BlendWeights:
    """A fitted (or prior) blend: intercepts (home, draw, away; away is 0) and coefficients."""

    intercepts: tuple[float, float, float]
    coefficients: tuple[tuple[float, ...], ...]
    """``coefficients[k][j]``: outcome ``OUTCOMES[k]``, input ``FEATURES[j]``."""
    n_train: int
    """Training matches (0 for the prior)."""
    l2: float

    @classmethod
    def from_arrays(cls, b: np.ndarray, w: np.ndarray, n_train: int, l2: float) -> BlendWeights:
        return cls(
            intercepts=(float(b[0]), float(b[1]), float(b[2])),
            coefficients=tuple(tuple(float(v) for v in row) for row in w),
            n_train=n_train,
            l2=l2,
        )

    @classmethod
    def prior(cls, w0: np.ndarray, l2: float) -> BlendWeights:
        return cls.from_arrays(np.zeros(_N_OUT), w0, 0, l2)

    def arrays(self) -> tuple[np.ndarray, np.ndarray]:
        return np.array(self.intercepts), np.array(self.coefficients)

    def probabilities(self, z: Sequence[float]) -> tuple[float, float, float]:
        b, w = self.arrays()
        s = b + w @ np.asarray(z, dtype=float)
        p = np.exp(s - s.max())
        p /= p.sum()
        return (float(p[0]), float(p[1]), float(p[2]))

    def as_dict(self) -> dict:
        """JSON-ready form (spec: ``model_versions.blend_weights``)."""
        return {
            "outcomes": list(OUTCOMES),
            "features": list(FEATURES),
            "intercepts": list(self.intercepts),
            "coefficients": [list(row) for row in self.coefficients],
            "n_train": self.n_train,
            "l2": self.l2,
        }


def _unpack(theta: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    b = np.array([theta[0], theta[1], 0.0])
    return b, theta[2:].reshape(_N_OUT, _N_FEAT)


def penalised_nll(
    theta: np.ndarray, x: np.ndarray, y: np.ndarray, w0: np.ndarray, l2: float
) -> tuple[float, np.ndarray]:
    """The objective and its gradient; ``theta`` = (b_home, b_draw, W flattened row-major)."""
    b, w = _unpack(theta)
    s = x @ w.T + b
    s -= s.max(axis=1, keepdims=True)
    log_z = np.log(np.exp(s).sum(axis=1))
    log_p = s - log_z[:, None]
    n = len(y)
    nll = -float(log_p[np.arange(n), y].sum())
    diff = w - w0
    value = nll + 0.5 * l2 * float((diff * diff).sum())
    g = np.exp(log_p)
    g[np.arange(n), y] -= 1.0
    grad_w = g.T @ x + l2 * diff
    grad_b = g.sum(axis=0)[:2]
    return value, np.concatenate([grad_b, grad_w.ravel()])


def fit_blend(
    x: np.ndarray,
    y: np.ndarray,
    *,
    l2: float = DEFAULT_L2,
    w0: np.ndarray | None = None,
    start: BlendWeights | None = None,
) -> BlendWeights:
    """Fit the penalised multinomial logit on inputs ``x`` (n × 7) and outcomes ``y`` (0 home,
    1 draw, 2 away). ``start`` warm-starts the optimiser (the objective is
    strictly convex for ``l2 > 0``, so the answer depends on it only within the tolerance)."""
    x = np.asarray(x, dtype=float)
    labels = np.asarray(y)
    if labels.ndim != 1 or not np.isin(labels, (0, 1, 2)).all():
        raise ValueError("outcomes must be a 1-D array of 0 (home), 1 (draw) or 2 (away)")
    y = labels.astype(int)
    if x.ndim != 2 or x.shape[1] != _N_FEAT or len(x) != len(y) or len(y) == 0:
        raise ValueError(f"need n × {_N_FEAT} inputs and n outcomes, n > 0; got {x.shape}")
    if l2 < 0:
        raise ValueError("l2 must be non-negative")
    w0 = prior_coefficients() if w0 is None else np.asarray(w0, dtype=float)
    if start is not None:
        b_start, w_start = start.arrays()
        theta0 = np.concatenate([b_start[:2] - b_start[2], w_start.ravel()])
    else:
        theta0 = np.concatenate([np.zeros(2), w0.ravel()])
    result = minimize(
        penalised_nll,
        theta0,
        args=(x, y, w0, l2),
        jac=True,
        method="L-BFGS-B",
        options={"maxiter": 2000, "gtol": 1e-8},
    )
    b, w = _unpack(result.x)
    return BlendWeights.from_arrays(b, w, len(y), l2)
