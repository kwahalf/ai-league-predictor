"""Command line for the ingest: backfill, one-off runs, and the daily Temporal Schedule.

Run inside the worker container of the stack you mean (it already has the Temporal address and
task queue in its environment), for example on dev:

    docker compose exec temporal-worker python -m src.football.ingest.cli backfill
    docker compose exec temporal-worker python -m src.football.ingest.cli run --season 2627
    docker compose exec temporal-worker python -m src.football.ingest.cli schedule

``backfill`` loads every season from 2016/17 to the current one, then fixtures.csv, and waits for
the result. ``schedule`` creates the daily 06:00 UTC schedule, or brings an existing one back to
this definition; running it again never creates a second schedule.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleCalendarSpec,
    SchedulePolicy,
    ScheduleOverlapPolicy,
    ScheduleRange,
    ScheduleSpec,
    ScheduleState,
    ScheduleUpdate,
)
from temporalio.common import WorkflowIDReusePolicy

from ...config import settings
from .models import FIRST_SEASON, IngestRequest, IngestSummary, season_for, season_range
from .workflow import IngestWorkflow

SCHEDULE_ID = "football-ingest-daily"
SCHEDULE_WORKFLOW_ID = "football-ingest-daily"
# A full backfill takes a few minutes; this only bounds a stuck run.
RUN_TIMEOUT = timedelta(hours=2)
DAILY_TIMEOUT = timedelta(hours=1)


def daily_schedule(task_queue: str) -> Schedule:
    """Every day at 06:00 UTC: the current season's file, then fixtures.csv."""
    return Schedule(
        action=ScheduleActionStartWorkflow(
            IngestWorkflow.run,
            IngestRequest(),
            id=SCHEDULE_WORKFLOW_ID,
            task_queue=task_queue,
            execution_timeout=DAILY_TIMEOUT,
        ),
        spec=ScheduleSpec(
            calendars=[
                ScheduleCalendarSpec(
                    hour=[ScheduleRange(6)],
                    minute=[ScheduleRange(0)],
                    comment="06:00 UTC daily",
                )
            ],
            time_zone_name="UTC",
        ),
        # A run still going at the next 06:00 (source site down for a day) isn't doubled up.
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP, catchup_window=timedelta(hours=12)),
        state=ScheduleState(note="Plan step 1.3: daily football-data.co.uk ingest (current season + fixtures.csv)"),
    )


async def ensure_schedule(client: Client, task_queue: str) -> str:
    """Create the daily schedule, or update it in place if it exists. Returns what it did."""
    schedule = daily_schedule(task_queue)
    try:
        await client.create_schedule(SCHEDULE_ID, schedule)
        return "created"
    except ScheduleAlreadyRunningError:
        handle = client.get_schedule_handle(SCHEDULE_ID)
        await handle.update(lambda _: ScheduleUpdate(schedule=schedule))
        return "updated"


async def run_ingest(client: Client, task_queue: str, request: IngestRequest, workflow_id: str) -> IngestSummary:
    return await client.execute_workflow(
        IngestWorkflow.run,
        request,
        id=workflow_id,
        task_queue=task_queue,
        execution_timeout=RUN_TIMEOUT,
        # Allow re-running after a finished run with the same id, never two at once.
        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
    )


def _print_summary(summary: IngestSummary) -> None:
    for result in summary.files:
        print(json.dumps(asdict(result), sort_keys=True))
    totals = {
        name: summary.total(name)
        for name in (
            "matches_in_file", "matches_inserted", "matches_updated", "stats_inserted", "stats_updated",
            "odds_inserted", "odds_updated", "odds_deleted", "teams_inserted", "teams_updated",
            "fixtures_skipped", "stale_skipped", "prices_dropped", "price_sets_nulled",
        )
    }
    print(json.dumps({"totals": totals}, sort_keys=True))


async def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.football.ingest.cli", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    backfill = sub.add_parser("backfill", help="Load a range of seasons (default 1617 to the current one), then fixtures.csv")
    backfill.add_argument("--from-season", default=FIRST_SEASON)
    backfill.add_argument("--to-season", default=None, help="default: the current season")
    backfill.add_argument("--no-fixtures", action="store_true")
    run = sub.add_parser("run", help="Load one season (default: current), then fixtures.csv")
    run.add_argument("--season", default=None)
    run.add_argument("--no-fixtures", action="store_true")
    sub.add_parser("schedule", help="Create or update the daily 06:00 UTC schedule")
    args = parser.parse_args(argv)

    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    queue = settings.temporal_task_queue

    if args.command == "schedule":
        outcome = await ensure_schedule(client, queue)
        print(f"Schedule {SCHEDULE_ID} {outcome}: daily at 06:00 UTC on task queue {queue}")
        return 0

    current = season_for(datetime.now(timezone.utc).date())
    if args.command == "backfill":
        seasons = season_range(args.from_season, args.to_season or current)
        workflow_id = f"football-ingest-backfill-{seasons[0]}-{seasons[-1]}"
    else:
        seasons = season_range(args.season or current, args.season or current)
        workflow_id = f"football-ingest-{seasons[0]}"
    request = IngestRequest(seasons=seasons, include_fixtures=not args.no_fixtures)
    print(f"Starting {workflow_id}: seasons {seasons[0]}..{seasons[-1]}, fixtures={request.include_fixtures}")
    _print_summary(await run_ingest(client, queue, request, workflow_id))
    return 0


def main() -> None:
    sys.exit(asyncio.run(_main(sys.argv[1:])))


if __name__ == "__main__":
    main()
