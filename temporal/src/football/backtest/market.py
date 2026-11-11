"""The market benchmark: consensus of margin-free pre-match 1X2 odds.

Used by the harness to score the market on the same matches as the model, and available to models
that use odds (the headline blend in step 2.5). Works on ``MatchView.odds``, which already holds
only pre-match quotes from ``MODEL_ODDS_SOURCES`` with prices <= 1.0 set to ``None``.
"""

from __future__ import annotations

import math
from typing import Iterable

from ..data import OddsQuote
from ..metrics import consensus, remove_margin
from .inputs import MODEL_ODDS_SOURCES


def margin_free_1x2(quote: OddsQuote | None) -> tuple[float, float, float] | None:
    """Margin-free (home, draw, away) probabilities of one quote, or ``None`` if any 1X2 price is
    missing or not a valid decimal odd (finite and > 1.0)."""
    if quote is None:
        return None
    prices = (quote.home, quote.draw, quote.away)
    if any(p is None or not math.isfinite(p) or p <= 1.0 for p in prices):
        return None
    probs = remove_margin(prices).probabilities  # type: ignore[arg-type]
    return (probs[0], probs[1], probs[2])


def market_consensus(quotes: Iterable[OddsQuote]) -> tuple[float, float, float] | None:
    """Equal-weight consensus of the margin-free 1X2 probabilities of ``MODEL_ODDS_SOURCES``.

    Sources without a usable 1X2 price are skipped; returns ``None`` if none is usable. Only
    pre-match quotes count: pass ``MatchView.odds`` (or quotes already filtered to pre-match).
    """
    by_source = {q.source: q for q in quotes if q.snapshot == "pre_match"}
    vectors = [margin_free_1x2(by_source.get(source)) for source in MODEL_ODDS_SOURCES]
    if all(v is None for v in vectors):
        return None
    probs = consensus(vectors)
    return (probs[0], probs[1], probs[2])
