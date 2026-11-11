"""Unit tests for market scoring (plan step 2.1b): pure scoring and the activity with a fake gateway."""

from __future__ import annotations

import json
import math

import pytest
from temporalio.exceptions import ApplicationError

from src.football.backtest.market import market_consensus
from src.football.data import OddsQuote
from src.football.metrics import brier, log_loss, remove_margin, rps
from src.football.scoring.activities import INPUTS_RPC, UPSERT_RPC, score_market
from src.football.scoring.market import outcome, score_match, score_season
from src.football.scoring.models import SeasonRef

PRICES = {
    ("B365", "pre_match"): (2.10, 3.40, 3.60),
    ("BW", "pre_match"): (2.05, 3.50, 3.70),
    ("AVG", "pre_match"): (2.08, 3.45, 3.55),
    ("PS", "pre_match"): (2.15, 3.50, 3.75),
    ("B365", "closing"): (1.95, 3.60, 4.00),
    ("BW", "closing"): (1.90, 3.70, 4.10),
    ("AVG", "closing"): (1.93, 3.65, 4.00),
    ("PS", "closing"): (1.98, 3.70, 4.20),
}


def _match(match_id="m1", home_goals=2, away_goals=1, status="finished", prices=PRICES):
    return {
        "match_id": match_id,
        "status": status,
        "home_goals": home_goals,
        "away_goals": away_goals,
        "odds": [
            {"source": s, "snapshot": snap, "home": h, "draw": d, "away": a}
            for (s, snap), (h, d, a) in prices.items()
        ],
    }


def _rows_by_key(rows):
    return {(r["bookmaker"], r["snapshot"]): r for r in rows}


def test_outcome_of_a_score():
    assert (outcome(2, 1), outcome(1, 1), outcome(0, 3)) == (0, 1, 2)


def test_every_source_and_the_consensus_are_scored_for_both_snapshots():
    rows, skipped = score_match(_match())
    assert skipped == 0
    by_key = _rows_by_key(rows)
    assert set(by_key) == {
        (s, snap) for s in ("B365", "BW", "AVG", "PS", None) for snap in ("pre_match", "closing")
    }
    b365 = by_key[("B365", "pre_match")]
    probs = remove_margin(PRICES[("B365", "pre_match")]).probabilities
    assert (b365["p_home"], b365["p_draw"], b365["p_away"]) == pytest.approx(probs, abs=0)
    assert b365["rps"] == rps(probs, 0)
    assert b365["log_loss"] == log_loss(probs, 0)
    assert b365["brier"] == brier(probs, 0)


def test_pre_match_consensus_is_exactly_the_backtest_market_benchmark():
    rows, _ = score_match(_match())
    quotes = [OddsQuote(s, snap, *p, None, None) for (s, snap), p in PRICES.items()]
    expected = market_consensus(quotes)
    row = _rows_by_key(rows)[(None, "pre_match")]
    assert (row["p_home"], row["p_draw"], row["p_away"]) == expected
    assert row["rps"] == rps(expected, 0)


def test_pinnacle_never_enters_the_consensus():
    without_ps = {k: v for k, v in PRICES.items() if k[0] != "PS"}
    with_ps = _rows_by_key(score_match(_match())[0])
    no_ps = _rows_by_key(score_match(_match(prices=without_ps))[0])
    for snap in ("pre_match", "closing"):
        assert with_ps[(None, snap)] == no_ps[(None, snap)]
    only_ps = {k: v for k, v in PRICES.items() if k[0] == "PS"}
    rows, _ = score_match(_match(prices=only_ps))
    assert {(r["bookmaker"], r["snapshot"]) for r in rows} == {("PS", "pre_match"), ("PS", "closing")}


def test_closing_consensus_uses_closing_prices_of_the_model_inputs():
    row = _rows_by_key(score_match(_match())[0])[(None, "closing")]
    vectors = [remove_margin(PRICES[(s, "closing")]).probabilities for s in ("B365", "BW", "AVG")]
    expected = tuple(math.fsum(v[i] for v in vectors) / 3 for i in range(3))
    assert (row["p_home"], row["p_draw"], row["p_away"]) == pytest.approx(expected, abs=1e-15)


def test_missing_or_bad_prices_are_skipped_and_counted():
    prices = dict(PRICES)
    prices[("BW", "pre_match")] = (1.0, 3.5, 3.7)  # <= 1.0 is not a decimal odd
    prices[("AVG", "pre_match")] = (None, None, None)
    rows, skipped = score_match(_match(prices=prices))
    by_key = _rows_by_key(rows)
    assert skipped == 2
    assert ("BW", "pre_match") not in by_key and ("AVG", "pre_match") not in by_key
    # The consensus is then just Bet365's margin-free 1X2.
    assert by_key[(None, "pre_match")]["p_home"] == pytest.approx(by_key[("B365", "pre_match")]["p_home"], abs=1e-15)


def test_unfinished_matches_are_not_scored_but_are_covered():
    scores = score_season([
        _match("m1"),
        _match("m2", None, None, status="scheduled"),
        _match("m3", prices={}),
    ])
    assert scores.match_ids == ["m1", "m2", "m3"]
    assert scores.matches_scored == 1
    assert {r["match_id"] for r in scores.rows} == {"m1"}
    assert len(scores.rows) == 10


def test_mean_rps_per_source_and_snapshot():
    scores = score_season([_match("m1", 2, 1), _match("m2", 0, 2)])
    means = scores.mean_rps()
    expected = [r["rps"] for r in scores.rows if r["bookmaker"] is None and r["snapshot"] == "pre_match"]
    assert means["consensus/pre_match"] == pytest.approx(sum(expected) / 2)
    assert set(means) == {f"{b}/{s}" for b in ("B365", "BW", "AVG", "PS", "consensus") for s in ("pre_match", "closing")}


def test_rows_survive_a_json_round_trip_exactly():
    """The RPC compares stored doubles with IS DISTINCT FROM; JSON floats must round-trip."""
    rows, _ = score_match(_match())
    assert json.loads(json.dumps(rows)) == rows


class FakeGateway:
    """In-memory stand-in for the two RPCs, with the same replace-and-guard semantics."""

    def __init__(self, matches):
        self.matches = matches
        self.stored: dict[tuple, dict] = {}
        self.calls: list[tuple[str, dict]] = []

    def rpc(self, function, args):
        self.calls.append((function, args))
        if function == INPUTS_RPC:
            return json.loads(json.dumps(self.matches))
        assert function == UPSERT_RPC
        args = json.loads(json.dumps(args))
        incoming = {(r["match_id"], r["bookmaker"], r["snapshot"]): r for r in args["p_scores"]}
        covered = set(args["p_match_ids"])
        deleted = [k for k in self.stored if k[0] in covered and k not in incoming]
        for k in deleted:
            del self.stored[k]
        inserted = updated = 0
        for k, row in incoming.items():
            if k not in self.stored:
                inserted += 1
            elif self.stored[k] != row:
                updated += 1
            self.stored[k] = row
        return {"inserted": inserted, "updated": updated, "deleted": len(deleted)}


def test_score_market_writes_once_and_a_rerun_changes_nothing():
    gateway = FakeGateway([_match("m1"), _match("m2", 1, 1)])
    first = score_market(gateway, SeasonRef("2324"))
    assert gateway.calls[0] == (INPUTS_RPC, {"p_division": "E0", "p_season": "2324"})
    assert (first.matches, first.matches_scored, first.rows) == (2, 2, 20)
    assert (first.inserted, first.updated, first.deleted) == (20, 0, 0)
    assert "consensus/pre_match" in first.mean_rps

    again = score_market(gateway, SeasonRef("2324"))
    assert (again.inserted, again.updated, again.deleted) == (0, 0, 0)

    # A quote disappears from the database: its stored score is removed.
    gateway.matches[0]["odds"] = [q for q in gateway.matches[0]["odds"] if q["source"] != "PS"]
    third = score_market(gateway, SeasonRef("2324"))
    assert (third.inserted, third.updated, third.deleted) == (0, 0, 2)


def test_score_market_rejects_a_bad_season_without_retrying():
    with pytest.raises(ApplicationError) as info:
        score_market(FakeGateway([]), SeasonRef("2016"))
    assert info.value.non_retryable


def test_corrupt_stored_data_fails_without_retrying():
    with pytest.raises(ApplicationError) as info:
        score_market(FakeGateway([{"status": "finished"}]), SeasonRef("2324"))
    assert info.value.non_retryable
