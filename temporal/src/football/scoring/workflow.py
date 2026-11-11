"""``MarketScoreWorkflow``: score the bookmakers on finished matches, one season at a time.

WHAT MAY NOT GO IN THIS FILE: HTTP, file or database access, ``datetime.now()``. Temporal replays
this code; the clock is ``workflow.now()`` and all I/O is in ``activities.py``. The activity is
called by name so tests can register fakes.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from ..ingest.models import season_for, validate_season
    from .models import (
        SCORE_MARKET_ACTIVITY,
        MarketScoreRequest,
        MarketScoreResult,
        MarketScoreSummary,
        SeasonRef,
    )

# Two database calls per season; retries cover a restarting stack.
SCORE_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=10),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=5),
    maximum_attempts=5,
)
SCORE_TIMEOUT = timedelta(minutes=5)


@workflow.defn
class MarketScoreWorkflow:
    @workflow.run
    async def run(self, request: MarketScoreRequest) -> MarketScoreSummary:
        try:
            seasons = [validate_season(s) for s in request.seasons] or [season_for(workflow.now().date())]
        except ValueError as exc:
            raise ApplicationError(str(exc), type="BadRequest", non_retryable=True) from exc

        summary = MarketScoreSummary()
        for season in seasons:
            result: MarketScoreResult = await workflow.execute_activity(
                SCORE_MARKET_ACTIVITY,
                SeasonRef(season=season, division=request.division),
                result_type=MarketScoreResult,
                start_to_close_timeout=SCORE_TIMEOUT,
                retry_policy=SCORE_RETRY,
            )
            summary.seasons.append(result)

        workflow.logger.info(
            "football market scoring done "
            f"seasons={len(summary.seasons)} rows={summary.total('rows')} "
            f"inserted={summary.total('inserted')} updated={summary.total('updated')} "
            f"deleted={summary.total('deleted')}"
        )
        return summary
