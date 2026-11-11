"""Penalised, time-weighted maximum-likelihood fit of Dixon–Coles. Arrays in, parameters out.

**Model.** For a match between home team ``h`` and away team ``a``::

    λ = exp(home_adv + attack[h] − defence[a])     (home expected goals)
    μ = exp(attack[a] − defence[h])                (away expected goals)
    P(x, y) = τ(x, y; λ, μ, ρ) · Poisson(x; λ) · Poisson(y; μ)

**Identifiability.** Adding the same constant to every attack and every defence leaves λ and μ
unchanged, so the fit fixes **mean attack = 0** over the teams in the fit. It does so by
optimising unconstrained ``attack_raw`` and using ``attack = attack_raw − mean(attack_raw)``.
Defence is then free: its mean sets the league's overall goal level (an average team's away
expected goals is ``exp(−mean(defence))``). A higher defence means fewer goals conceded.

**Objective.** Minimise ``(−Σ wₖ·log Pₖ + penalty) / Σ wₖ`` over attack, defence, home advantage
and ρ, where ``wₖ`` is the match's time weight. The penalty is a Gaussian prior per team::

    penalty = Σᵢ kᵢ/2 · [(attack[i] − m_attack[i])² + (defence[i] − mean(defence) − m_defence[i])²]

i.e. each team's attack is pulled toward ``m_attack[i]`` and its defence toward the league mean
defence plus ``m_defence[i]``, with precision ``kᵢ``. The model uses this for promoted teams
(see ``model.py``) and a very weak pull toward the league average for everyone else, which keeps
the fit finite for a team with a freak record (e.g. no goals conceded in its few matches).

The gradient is analytic; ``scipy.optimize.minimize`` (L-BFGS-B) bounds ρ to ``RHO_BOUNDS``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln

RHO_BOUNDS = (-0.3, 0.3)
"""Search range for ρ. Published Premier League estimates are around −0.05 to −0.15."""

_TAU_FLOOR = 1e-10


@dataclass(frozen=True)
class FitData:
    """Matches as index arrays. Team ``i`` is ``teams[i]`` in the caller's list."""

    home: np.ndarray
    away: np.ndarray
    home_goals: np.ndarray
    away_goals: np.ndarray
    weights: np.ndarray
    n_teams: int


@dataclass(frozen=True)
class TeamPrior:
    """Per-team Gaussian prior: means (relative to the league as described above) and precision."""

    attack_mean: np.ndarray
    defence_mean: np.ndarray
    precision: np.ndarray


@dataclass(frozen=True)
class FittedParameters:
    attack: np.ndarray
    """Mean zero over the teams in the fit."""
    defence: np.ndarray
    home_adv: float
    rho: float
    converged: bool
    neg_log_likelihood: float
    """The minimised objective (per unit of weight, penalty included)."""


def _unpack(theta: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray, float, float]:
    raw = theta[:n]
    return raw - raw.mean(), theta[n : 2 * n], float(theta[2 * n]), float(theta[2 * n + 1])


def _objective(theta: np.ndarray, data: FitData, prior: TeamPrior | None, total_weight: float):
    n = data.n_teams
    attack, defence, home_adv, rho = _unpack(theta, n)
    h, a, x, y, w = data.home, data.away, data.home_goals, data.away_goals, data.weights

    log_lam = home_adv + attack[h] - defence[a]
    log_mu = attack[a] - defence[h]
    lam, mu = np.exp(log_lam), np.exp(log_mu)

    # Dixon–Coles τ and its partial derivatives, only for the four low-score cells.
    tau = np.ones_like(lam)
    d_lam = np.zeros_like(lam)  # ∂ log τ / ∂λ
    d_mu = np.zeros_like(lam)  # ∂ log τ / ∂μ
    d_rho = np.zeros_like(lam)  # ∂ log τ / ∂ρ
    c00 = (x == 0) & (y == 0)
    c01 = (x == 0) & (y == 1)
    c10 = (x == 1) & (y == 0)
    c11 = (x == 1) & (y == 1)
    tau[c00] = 1.0 - lam[c00] * mu[c00] * rho
    tau[c01] = 1.0 + lam[c01] * rho
    tau[c10] = 1.0 + mu[c10] * rho
    tau[c11] = 1.0 - rho
    tau = np.maximum(tau, _TAU_FLOOR)
    d_lam[c00] = -mu[c00] * rho / tau[c00]
    d_mu[c00] = -lam[c00] * rho / tau[c00]
    d_rho[c00] = -lam[c00] * mu[c00] / tau[c00]
    d_lam[c01] = rho / tau[c01]
    d_rho[c01] = lam[c01] / tau[c01]
    d_mu[c10] = rho / tau[c10]
    d_rho[c10] = mu[c10] / tau[c10]
    d_rho[c11] = -1.0 / tau[c11]

    # Log-likelihood without the constant −log(x!) − log(y!) (added back for reporting only).
    ll = w * (x * log_lam - lam + y * log_mu - mu + np.log(tau))
    value = -ll.sum()

    g_log_lam = w * (x - lam + lam * d_lam)  # ∂ll/∂log λ
    g_log_mu = w * (y - mu + mu * d_mu)  # ∂ll/∂log μ
    g_attack = np.bincount(h, g_log_lam, n) + np.bincount(a, g_log_mu, n)
    g_defence = -np.bincount(a, g_log_lam, n) - np.bincount(h, g_log_mu, n)
    g_home = g_log_lam.sum()
    g_rho = (w * d_rho).sum()

    grad_attack = -g_attack
    grad_defence = -g_defence
    if prior is not None:
        k = prior.precision
        r_att = attack - prior.attack_mean
        r_def = defence - defence.mean() - prior.defence_mean
        value += 0.5 * float((k * (r_att**2 + r_def**2)).sum())
        grad_attack = grad_attack + k * r_att
        grad_defence = grad_defence + k * r_def - (k * r_def).sum() / n

    # attack = raw − mean(raw): the gradient w.r.t. raw is the centred gradient w.r.t. attack.
    grad_raw = grad_attack - grad_attack.mean()
    grad = np.concatenate([grad_raw, grad_defence, [-g_home, -g_rho]])
    return value / total_weight, grad / total_weight


def fit_parameters(
    data: FitData,
    prior: TeamPrior | None = None,
    *,
    initial: np.ndarray | None = None,
    max_iter: int = 1000,
) -> FittedParameters:
    """Fit attack, defence, home advantage and ρ by penalised weighted maximum likelihood."""
    n = data.n_teams
    if len(data.home) == 0:
        raise ValueError("no matches to fit")
    total_weight = float(data.weights.sum())
    if not total_weight > 0:
        raise ValueError("match weights must sum to a positive number")
    if initial is None:
        mean_away = max(float(np.average(data.away_goals, weights=data.weights)), 0.1)
        mean_home = max(float(np.average(data.home_goals, weights=data.weights)), 0.1)
        initial = np.concatenate(
            [np.zeros(n), np.full(n, -np.log(mean_away)), [np.log(mean_home / mean_away), 0.0]]
        )
    bounds = [(None, None)] * (2 * n + 1) + [RHO_BOUNDS]
    result = minimize(
        _objective,
        initial,
        args=(data, prior, total_weight),
        jac=True,
        method="L-BFGS-B",
        bounds=bounds,
        options={"maxiter": max_iter, "ftol": 1e-12, "gtol": 1e-8},
    )
    attack, defence, home_adv, rho = _unpack(result.x, n)
    log_factorials = gammaln(data.home_goals + 1) + gammaln(data.away_goals + 1)
    constant = float((data.weights * log_factorials).sum())
    return FittedParameters(
        attack=attack,
        defence=defence,
        home_adv=home_adv,
        rho=rho,
        converged=bool(result.success),
        neg_log_likelihood=float(result.fun) + constant / total_weight,
    )
