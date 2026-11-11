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
consensus: before any training (``BlendWeights.prior``, intercepts 0) the blend gives back the
consensus exactly. Once fitted, the coefficients move away from ``W₀`` only as far as the
out-of-sample record supports, but the unpenalised intercepts are always fitted, so even an
infinitely strong penalty gives the consensus with a fitted base-rate shift (a constant tilt of
the home/draw/away log-odds), not the consensus itself. This is
an a-priori choice, not a result of tuning: betting markets are the strongest public forecasters
of football results, and the headline's target is to at least match the consensus. Centring the
penalty on zero instead would shrink towards a constant forecast (uniform up to the intercepts),
a far worse forecaster than either input. ``DEFAULT_L2`` = 10 is roughly a season's worth of
evidence on the fundamentals-versus-market contrast (that contrast varies little between matches,
so it is the least identified direction); step 2.6 tunes it walk-forward.

**Structures** (step 2.6). ``"full"`` is the model above: 21 free coefficients. ``"pool"`` has
three: ``W = α·B_market + β·B_fundamentals + γ·B_elo``, where ``B_market`` puts 1 on
``log m_k`` for outcome ``k`` (likewise ``B_fundamentals`` on ``log f_k``) and ``B_elo`` puts +1 on
the Elo difference for home and −1 for away. That is a log-linear pool of market and fundamentals
plus an Elo tilt, with the same unpenalised intercepts. The penalty is then
``(l2 / 2) · ‖θ − θ₀‖²`` on ``θ = (α, β, γ)``, centred on the same prior
(``θ₀ = (market_weight, fundamentals_weight, 0)``).

**Sample weights** (step 2.6, optional). Each training match's log-likelihood term can be
weighted, e.g. by ``0.5 ** (age / half_life)`` to favour recent seasons (``BlendModel``'s
``train_half_life_days``). The weights are used as they are (not rescaled), so the penalty keeps
its meaning of "``l2`` units of full-weight evidence".

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

STRUCTURES: tuple[str, ...] = ("full", "pool")
POOL_PARAMETERS: tuple[str, ...] = ("market", "fundamentals", "elo")


def _pool_basis() -> np.ndarray:
    """The three 3 × 7 basis matrices of the ``"pool"`` structure (market, fundamentals, Elo)."""
    basis = np.zeros((3, _N_OUT, _N_FEAT))
    for k in range(_N_OUT):
        basis[0, k, 3 + k] = 1.0
        basis[1, k, k] = 1.0
    basis[2, 0, 6] = 1.0
    basis[2, 2, 6] = -1.0
    return basis


POOL_BASIS = _pool_basis()


def _basis(structure: str) -> np.ndarray | None:
    if structure == "full":
        return None
    if structure == "pool":
        return POOL_BASIS
    raise ValueError(f"unknown blend structure {structure!r}; expected one of {STRUCTURES}")


def _to_parameters(w: np.ndarray, basis: np.ndarray | None) -> np.ndarray:
    """Coefficients ``w`` (3 × 7) in the structure's parameters (least squares onto the basis)."""
    if basis is None:
        return np.asarray(w, dtype=float).ravel()
    a = basis.reshape(len(basis), -1).T
    return np.linalg.lstsq(a, np.asarray(w, dtype=float).ravel(), rcond=None)[0]


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
    structure: str = "full"
    """``"full"`` (21 free coefficients) or ``"pool"`` (3); see the module docstring."""
    train_half_life_days: float | None = None
    """Half-life of the training rows' weights, or ``None`` for equal weights."""

    @classmethod
    def from_arrays(
        cls,
        b: np.ndarray,
        w: np.ndarray,
        n_train: int,
        l2: float,
        structure: str = "full",
        train_half_life_days: float | None = None,
    ) -> BlendWeights:
        return cls(
            intercepts=(float(b[0]), float(b[1]), float(b[2])),
            coefficients=tuple(tuple(float(v) for v in row) for row in w),
            n_train=n_train,
            l2=l2,
            structure=structure,
            train_half_life_days=train_half_life_days,
        )

    @classmethod
    def prior(
        cls,
        w0: np.ndarray,
        l2: float,
        structure: str = "full",
        train_half_life_days: float | None = None,
    ) -> BlendWeights:
        return cls.from_arrays(np.zeros(_N_OUT), w0, 0, l2, structure, train_half_life_days)

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
            "structure": self.structure,
            "train_half_life_days": self.train_half_life_days,
        }


def _unpack(theta: np.ndarray, basis: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    b = np.array([theta[0], theta[1], 0.0])
    if basis is None:
        return b, theta[2:].reshape(_N_OUT, _N_FEAT)
    return b, np.tensordot(theta[2:], basis, axes=1)


def penalised_nll(
    theta: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    w0: np.ndarray,
    l2: float,
    sample_weight: np.ndarray | None = None,
    basis: np.ndarray | None = None,
) -> tuple[float, np.ndarray]:
    """The objective and its gradient; ``theta`` = (b_home, b_draw, parameters), where the
    parameters are W flattened row-major (``basis`` None, the full structure) or the basis
    weights. ``w0`` is the prior in the same parameters (a 3 × 7 matrix is accepted for full)."""
    b, w = _unpack(theta, basis)
    s = x @ w.T + b
    s -= s.max(axis=1, keepdims=True)
    log_z = np.log(np.exp(s).sum(axis=1))
    log_p = s - log_z[:, None]
    n = len(y)
    sw = np.ones(n) if sample_weight is None else np.asarray(sample_weight, dtype=float)
    nll = -float((sw * log_p[np.arange(n), y]).sum())
    diff = theta[2:] - np.asarray(w0, dtype=float).ravel()
    value = nll + 0.5 * l2 * float((diff * diff).sum())
    g = np.exp(log_p)
    g[np.arange(n), y] -= 1.0
    g *= sw[:, None]
    grad_w = g.T @ x
    if basis is not None:
        grad_w = np.tensordot(basis, grad_w, axes=([1, 2], [0, 1]))
    grad_params = grad_w.ravel() + l2 * diff
    grad_b = g.sum(axis=0)[:2]
    return value, np.concatenate([grad_b, grad_params])


def fit_blend(
    x: np.ndarray,
    y: np.ndarray,
    *,
    l2: float = DEFAULT_L2,
    w0: np.ndarray | None = None,
    start: BlendWeights | None = None,
    sample_weight: np.ndarray | None = None,
    structure: str = "full",
    train_half_life_days: float | None = None,
) -> BlendWeights:
    """Fit the penalised multinomial logit on inputs ``x`` (n × 7) and outcomes ``y`` (0 home,
    1 draw, 2 away). ``start`` warm-starts the optimiser (the objective is
    strictly convex for ``l2 > 0``, so the answer depends on it only within the tolerance).
    ``sample_weight`` (n, non-negative) weights each match; ``structure`` is ``"full"`` or
    ``"pool"``; ``train_half_life_days`` is only recorded in the result."""
    x = np.asarray(x, dtype=float)
    labels = np.asarray(y)
    if labels.ndim != 1 or not np.isin(labels, (0, 1, 2)).all():
        raise ValueError("outcomes must be a 1-D array of 0 (home), 1 (draw) or 2 (away)")
    y = labels.astype(int)
    if x.ndim != 2 or x.shape[1] != _N_FEAT or len(x) != len(y) or len(y) == 0:
        raise ValueError(f"need n × {_N_FEAT} inputs and n outcomes, n > 0; got {x.shape}")
    if l2 < 0:
        raise ValueError("l2 must be non-negative")
    if sample_weight is not None:
        sample_weight = np.asarray(sample_weight, dtype=float)
        if sample_weight.shape != y.shape or not np.all(np.isfinite(sample_weight)):
            raise ValueError("sample_weight must be n finite numbers")
        if (sample_weight < 0).any() or not sample_weight.sum() > 0:
            raise ValueError("sample_weight must be non-negative with a positive sum")
    basis = _basis(structure)
    w0 = prior_coefficients() if w0 is None else np.asarray(w0, dtype=float)
    c0 = _to_parameters(w0, basis)
    if basis is not None and not np.allclose(np.tensordot(c0, basis, axes=1), w0):
        raise ValueError(f"the prior w0 can't be expressed in the {structure!r} structure")
    if start is not None:
        b_start, w_start = start.arrays()
        theta0 = np.concatenate([b_start[:2] - b_start[2], _to_parameters(w_start, basis)])
    else:
        theta0 = np.concatenate([np.zeros(2), c0])
    result = minimize(
        penalised_nll,
        theta0,
        args=(x, y, c0, l2, sample_weight, basis),
        jac=True,
        method="L-BFGS-B",
        options={"maxiter": 2000, "gtol": 1e-8},
    )
    b, w = _unpack(result.x, basis)
    return BlendWeights.from_arrays(b, w, len(y), l2, structure, train_half_life_days)
