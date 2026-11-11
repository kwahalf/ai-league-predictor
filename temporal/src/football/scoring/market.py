"""Score the bookmakers on finished matches: pure, no I/O.

For each finished match, and for each snapshot (``pre_match``, ``closing``):

- every source in ``SCORED_SOURCES`` (the model inputs ``B365``, ``BW``, ``AVG`` and the
  benchmark-only Pinnacle ``PS``) that has a usable 1X2 quote gets one row;
- the **consensus** gets one row (``bookmaker = None``) if any model-input source is usable. It is
  the backtest's market benchmark (``backtest/market.py``): the equal-weight average of the
  margin-free 1X2 of ``B365``, ``BW`` and ``AVG``, skipping a source whose price is missing or
  <= 1.0. Pinnacle never enters it.

Each row holds the margin-free probabilities and their RPS, log loss and Brier score (2.1a metrics).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ..backtest.inputs import MODEL_ODDS_SOURCES
from ..metrics import AWAY, DRAW, HOME, brier, consensus, log_loss, remove_margin, rps

SCORED_SOURCES: tuple[str, ...] = (*MODEL_ODDS_SOURCES, "PS")
"""Sources scored individually: the model inputs plus Pinnacle (benchmark only)."""

SCORED_SNAPSHOTS: tuple[str, ...] = ("pre_match", "closing")

CONSENSUS = "consensus"
"""Label of the consensus in ``mean_rps`` keys; stored as ``bookmaker = None``."""


@dataclass(frozen=True)
class SeasonScores:
    match_ids: list[str]
    """Every match given, scored or not: the RPC replaces the stored scores of all of them."""
    rows: list[dict[str, Any]]
    matches_scored: int
    quotes_skipped: int

    def mean_rps(self) -> dict[str, float]:
        by_key: dict[str, list[float]] = defaultdict(list)
        for row in self.rows:
            by_key[f"{row['bookmaker'] or CONSENSUS}/{row['snapshot']}"].append(row["rps"])
        return {key: math.fsum(values) / len(values) for key, values in sorted(by_key.items())}


def outcome(home_goals: int, away_goals: int) -> int:
    """1X2 outcome index (home, draw, away) of a final score."""
    if home_goals > away_goals:
        return HOME
    if home_goals == away_goals:
        return DRAW
    return AWAY


def _usable(price: Any) -> bool:
    return isinstance(price, (int, float)) and not isinstance(price, bool) and math.isfinite(price) and price > 1.0


def margin_free(quote: Mapping[str, Any] | None) -> tuple[float, float, float] | None:
    """Margin-free (home, draw, away) of a quote, or ``None`` if any 1X2 price is missing or <= 1.0."""
    if quote is None:
        return None
    prices = (quote.get("home"), quote.get("draw"), quote.get("away"))
    if not all(_usable(p) for p in prices):
        return None
    p = remove_margin([float(x) for x in prices]).probabilities
    return (p[0], p[1], p[2])


def _row(match_id: str, bookmaker: str | None, snapshot: str, probs: Sequence[float], result: int) -> dict[str, Any]:
    return {
        "match_id": match_id,
        "bookmaker": bookmaker,
        "snapshot": snapshot,
        "p_home": probs[0],
        "p_draw": probs[1],
        "p_away": probs[2],
        "rps": rps(probs, result),
        "log_loss": log_loss(probs, result),
        "brier": brier(probs, result),
    }


def score_match(match: Mapping[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """The market_scores rows of one match (none unless finished), and how many quotes were unusable."""
    if match.get("status") != "finished" or match.get("home_goals") is None or match.get("away_goals") is None:
        return [], 0
    result = outcome(int(match["home_goals"]), int(match["away_goals"]))
    match_id = str(match["match_id"])
    quotes = {(q.get("source"), q.get("snapshot")): q for q in match.get("odds") or ()}
    rows: list[dict[str, Any]] = []
    skipped = 0
    for snapshot in SCORED_SNAPSHOTS:
        probs_by_source: dict[str, tuple[float, float, float] | None] = {}
        for source in SCORED_SOURCES:
            quote = quotes.get((source, snapshot))
            probs = margin_free(quote)
            if quote is not None and probs is None:
                skipped += 1
            probs_by_source[source] = probs
            if probs is not None:
                rows.append(_row(match_id, source, snapshot, probs, result))
        inputs = [probs_by_source[s] for s in MODEL_ODDS_SOURCES]
        if any(v is not None for v in inputs):
            rows.append(_row(match_id, None, snapshot, consensus(inputs), result))
    return rows, skipped


def score_season(matches: Iterable[Mapping[str, Any]]) -> SeasonScores:
    """Score every finished match of a season (as returned by ``football_market_score_inputs``)."""
    match_ids: list[str] = []
    rows: list[dict[str, Any]] = []
    scored = skipped = 0
    for match in matches:
        match_ids.append(str(match["match_id"]))
        match_rows, match_skipped = score_match(match)
        rows.extend(match_rows)
        skipped += match_skipped
        scored += bool(match_rows)
    return SeasonScores(match_ids=match_ids, rows=rows, matches_scored=scored, quotes_skipped=skipped)
