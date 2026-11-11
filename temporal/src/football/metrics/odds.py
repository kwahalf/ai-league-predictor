"""Bookmaker odds to probabilities: margin removal and the consensus of several sources.

See the spec, "Removing the margin": ``1/odds`` sums to more than 1; the excess is the bookmaker's
margin. The proportional method divides each ``1/odds`` by the sum, so the result sums to 1.

Shin's method (``remove_margin_shin``; Shin 1993) is the alternative the spec's open question
names: it models the margin as protection against a share ``z`` of insider money, which takes
more margin off longshots than off favourites (the favourite–longshot bias). Step 2.6 compared the
two on the tuning seasons; see the spec's "Open Questions" for the outcome.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

from ._validation import MetricsError, check_probs


@dataclass(frozen=True)
class MarginFree:
    probabilities: tuple[float, ...]
    """Margin-free probabilities, same order as the odds; they sum to 1."""
    margin: float
    """Bookmaker margin (overround): sum(1/odds) - 1. 0.05 means 5%."""


def remove_margin(odds: Sequence[float]) -> MarginFree:
    """Proportional margin removal from decimal odds: p_i = (1/o_i) / sum_j(1/o_j).

    Works for any number of outcomes: 3 for 1X2 (home, draw, away), 2 for over/under.
    Every price must be a finite decimal odd greater than 1.0.
    """
    try:
        prices = tuple(float(o) for o in odds)
    except (TypeError, ValueError) as exc:
        raise MetricsError(f"odds must be a sequence of numbers, got {odds!r}") from exc
    if len(prices) < 2:
        raise MetricsError(f"odds need at least 2 outcomes, got {len(prices)}")
    for o in prices:
        if not math.isfinite(o) or o <= 1.0:
            raise MetricsError(f"decimal odds must be finite and > 1.0, got {prices!r}")
    implied = [1.0 / o for o in prices]
    total = math.fsum(implied)
    return MarginFree(
        probabilities=tuple(p / total for p in implied),
        margin=total - 1.0,
    )


def remove_margin_shin(odds: Sequence[float], *, tol: float = 1e-12) -> MarginFree:
    """Shin's margin removal: ``p_i = (sqrt(z² + 4(1 − z)·π_i²/Π) − z) / (2(1 − z))`` with
    ``π_i = 1/o_i`` and ``Π = Σ π_i``, where the insider share ``z`` in [0, 1) is the one that makes
    the ``p_i`` sum to 1 (found by bisection; the sum falls as ``z`` grows). With no margin
    (``Π ≤ 1``) ``z`` is 0 and the result is the proportional one. Same validation as
    ``remove_margin``."""
    base = remove_margin(odds)  # validates; also the answer when there is no margin
    if base.margin <= 0.0:
        return base
    implied = [1.0 / float(o) for o in odds]
    total = math.fsum(implied)

    def probs(z: float) -> list[float]:
        return [
            (math.sqrt(z * z + 4.0 * (1.0 - z) * pi * pi / total) - z) / (2.0 * (1.0 - z))
            for pi in implied
        ]

    lo, hi = 0.0, 1.0 - 1e-12
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if math.fsum(probs(mid)) > 1.0:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    p = probs(0.5 * (lo + hi))
    s = math.fsum(p)  # 1 within the tolerance; divide so it is 1 to rounding
    return MarginFree(probabilities=tuple(x / s for x in p), margin=base.margin)


def consensus(
    sources: Sequence[Optional[Sequence[float]]],
    weights: Optional[Sequence[float]] = None,
) -> tuple[float, ...]:
    """Weighted average of the margin-free probabilities of the sources that priced the match.

    ``sources`` holds one margin-free probability vector per source (e.g. Bet365, Bwin, market
    average), or None where a source has no odds for the match; None entries are ignored.
    ``weights`` (one per source, same order) defaults to equal weights. The weights of the
    sources present are renormalised to sum to 1, so a missing source doesn't shrink the result.

    Raises ``MetricsError`` if no source priced the match, the vectors have different lengths, or
    the present sources' weights are negative or all zero.
    """
    sources = list(sources)
    if weights is None:
        weights = [1.0] * len(sources)
    else:
        try:
            weights = [float(w) for w in weights]
        except (TypeError, ValueError) as exc:
            raise MetricsError(f"weights must be a sequence of numbers, got {weights!r}") from exc
        if len(weights) != len(sources):
            raise MetricsError(
                f"weights must have one entry per source: {len(weights)} weights, "
                f"{len(sources)} sources"
            )
    present = [
        (check_probs(s, name=f"sources[{i}]"), w)
        for i, (s, w) in enumerate(zip(sources, weights))
        if s is not None
    ]
    if not present:
        raise MetricsError("no source priced this match; consensus needs at least one")
    n = len(present[0][0])
    if any(len(p) != n for p, _ in present):
        raise MetricsError(f"all sources must have the same number of outcomes, got {sources!r}")
    if any(not math.isfinite(w) or w < 0.0 for _, w in present):
        raise MetricsError(f"weights must be finite and non-negative, got {weights!r}")
    max_weight = max(w for _, w in present)
    if max_weight <= 0.0:
        raise MetricsError("the weights of the sources that priced this match are all zero")
    # Scale by the largest weight first, so tiny or huge weights can't underflow or overflow.
    scaled = [(p, w / max_weight) for p, w in present]
    total_weight = math.fsum(w for _, w in scaled)
    return tuple(math.fsum(p[i] * w for p, w in scaled) / total_weight for i in range(n))
