"""``IngestWorkflow``: load football-data.co.uk season files and fixtures.csv into Supabase.

For each season, in order: fetch (download + store raw) then load (parse + upsert). Then, if asked,
the same for ``fixtures.csv`` for the current season. Season files go first so that a match that
already has a result is never turned back into a fixture.

WHAT MAY NOT GO IN THIS FILE: HTTP, file or database access, ``datetime.now()``. Temporal replays
this code; the clock is ``workflow.now()`` and all I/O is in ``activities.py``. Activities are
called by name so tests can register fakes.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from .models import (
        FETCH_ACTIVITY,
        FIXTURES_KIND,
        LOAD_ACTIVITY,
        SEASON_KIND,
        CsvSource,
        IngestRequest,
        IngestSummary,
        LoadResult,
        RawCsvRef,
        season_for,
        validate_season,
    )

# football-data.co.uk sometimes answers 503 for a while; back off up to ~10 minutes between tries.
FETCH_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=30),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=10),
    maximum_attempts=6,
)
FETCH_TIMEOUT = timedelta(minutes=3)
# The load is one database call per file; retries cover a restarting stack.
LOAD_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=10),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=5),
    maximum_attempts=5,
)
LOAD_TIMEOUT = timedelta(minutes=5)


@workflow.defn
class IngestWorkflow:
    @workflow.run
    async def run(self, request: IngestRequest) -> IngestSummary:
        current = season_for(workflow.now().date())
        try:
            seasons = [validate_season(s) for s in request.seasons] or [current]
        except ValueError as exc:
            raise ApplicationError(str(exc), type="BadRequest", non_retryable=True) from exc

        summary = IngestSummary()
        for season in seasons:
            summary.files.append(await self._ingest(CsvSource(SEASON_KIND, season, request.division)))
        if request.include_fixtures:
            summary.files.append(await self._ingest(CsvSource(FIXTURES_KIND, current, request.division)))

        workflow.logger.info(
            "football ingest done "
            f"files={len(summary.files)} matches_inserted={summary.total('matches_inserted')} "
            f"matches_updated={summary.total('matches_updated')} odds_inserted={summary.total('odds_inserted')} "
            f"odds_updated={summary.total('odds_updated')} prices_dropped={summary.total('prices_dropped')} "
            f"price_sets_nulled={summary.total('price_sets_nulled')}"
        )
        return summary

    async def _ingest(self, source: CsvSource) -> LoadResult:
        ref: RawCsvRef = await workflow.execute_activity(
            FETCH_ACTIVITY,
            source,
            result_type=RawCsvRef,
            start_to_close_timeout=FETCH_TIMEOUT,
            retry_policy=FETCH_RETRY,
        )
        return await workflow.execute_activity(
            LOAD_ACTIVITY,
            ref,
            result_type=LoadResult,
            start_to_close_timeout=LOAD_TIMEOUT,
            retry_policy=LOAD_RETRY,
        )
