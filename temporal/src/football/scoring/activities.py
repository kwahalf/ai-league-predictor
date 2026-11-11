"""Market-scoring activity: all of its I/O.

``football_score_market`` reads one season's matches, results and pre-match/closing 1X2 prices
(``football_market_score_inputs`` RPC), scores them (``market.py``) and writes ``market_scores`` in
one call to ``football_upsert_market_scores``, which only writes rows whose values changed. Step 2.7
runs the same activity after each ingest.

The inputs come with a revision (a hash of them) that goes back with the scores. If the inputs
changed in between (an ingest, or an overlapping run), the RPC writes nothing and reports the batch
stale; the activity then fails with a retryable error, and the retry re-reads and re-scores.

Synchronous (blocking HTTP), so the worker runs it on its thread pool. Built by
``build_activities`` so tests can inject a fake gateway, like the ingest activities.
"""

from __future__ import annotations

from typing import Any, Protocol

from temporalio import activity
from temporalio.exceptions import ApplicationError

from ..ingest.models import validate_season
from .market import score_season
from .models import SCORE_MARKET_ACTIVITY, MarketScoreResult, SeasonRef

INPUTS_RPC = "football_market_score_inputs"
UPSERT_RPC = "football_upsert_market_scores"


class RpcGateway(Protocol):
    """What the activity needs from Supabase. The ingest's ``HttpGateway`` is the real one."""

    def rpc(self, function: str, args: dict[str, Any]) -> Any: ...


def _log(message: str) -> None:
    if activity.in_activity():
        activity.logger.info(message)


def score_market(gateway: RpcGateway, ref: SeasonRef) -> MarketScoreResult:
    try:
        validate_season(ref.season)
    except ValueError as exc:
        raise ApplicationError(str(exc), type="BadRequest", non_retryable=True) from exc
    if not ref.division.isalnum():
        raise ApplicationError(f"Bad division {ref.division!r}", type="BadRequest", non_retryable=True)

    inputs = gateway.rpc(INPUTS_RPC, {"p_division": ref.division, "p_season": ref.season}) or {}
    try:
        revision = inputs["revision"]
        scores = score_season(inputs["matches"])
    except (KeyError, TypeError, ValueError) as exc:
        # Bad stored data doesn't improve on retry.
        raise ApplicationError(
            f"Cannot score season {ref.season}: {exc}", type="ScoringError", non_retryable=True
        ) from exc

    counts = gateway.rpc(
        UPSERT_RPC,
        {"p_division": ref.division, "p_season": ref.season, "p_revision": revision, "p_scores": scores.rows},
    ) or {}
    if counts.get("stale"):
        # Retryable: the next attempt reads the new inputs.
        raise ApplicationError(
            f"Market inputs of season {ref.season} changed while it was being scored; nothing written",
            type="StaleInputs",
        )
    result = MarketScoreResult(
        season=ref.season,
        matches=len(scores.match_ids),
        matches_scored=scores.matches_scored,
        rows=len(scores.rows),
        quotes_skipped=scores.quotes_skipped,
        inserted=int(counts.get("inserted", 0)),
        updated=int(counts.get("updated", 0)),
        deleted=int(counts.get("deleted", 0)),
        mean_rps=scores.mean_rps(),
    )
    _log(
        f"football market scores season={result.season} matches={result.matches} "
        f"matches_scored={result.matches_scored} rows={result.rows} quotes_skipped={result.quotes_skipped} "
        f"inserted={result.inserted} updated={result.updated} deleted={result.deleted} "
        f"consensus_pre_match_rps={result.mean_rps.get('consensus/pre_match')}"
    )
    return result


def build_activities(gateway: RpcGateway | None = None) -> list:
    """The scoring activities, bound to ``gateway`` (default: the real Supabase one)."""

    def _gateway() -> RpcGateway:
        if gateway is not None:
            return gateway
        from ...config import settings
        from ..ingest.gateway import HttpGateway

        return HttpGateway(settings.supabase_url, settings.supabase_service_role_key)

    @activity.defn(name=SCORE_MARKET_ACTIVITY)
    def football_score_market(ref: SeasonRef) -> MarketScoreResult:
        return score_market(_gateway(), ref)

    return [football_score_market]
