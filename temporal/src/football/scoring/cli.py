"""Command line for market scoring: the backfill and one-off runs.

Run inside the worker container of the stack you mean, for example on dev:

    docker compose exec temporal-worker python -m src.football.scoring.cli market
    docker compose exec temporal-worker python -m src.football.scoring.cli market --season 2627

``market`` scores every season from 2016/17 to the current one (or ``--season``, or a
``--from-season``/``--to-season`` range) through ``MarketScoreWorkflow`` and waits for the result.
Running it again with unchanged data changes nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy

from ...config import settings
from ..ingest.models import FIRST_SEASON, season_for, season_range
from .models import MarketScoreRequest, MarketScoreSummary
from .workflow import MarketScoreWorkflow

RUN_TIMEOUT = timedelta(hours=1)


def seasons_for(args: argparse.Namespace, current: str) -> list[str]:
    if args.season:
        return season_range(args.season, args.season)
    return season_range(args.from_season, args.to_season or current)


async def run_market(client: Client, task_queue: str, request: MarketScoreRequest, workflow_id: str) -> MarketScoreSummary:
    return await client.execute_workflow(
        MarketScoreWorkflow.run,
        request,
        id=workflow_id,
        task_queue=task_queue,
        execution_timeout=RUN_TIMEOUT,
        # Allow re-running after a finished run with the same id, never two at once.
        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
    )


def _print_summary(summary: MarketScoreSummary) -> None:
    for result in summary.seasons:
        print(json.dumps(asdict(result), sort_keys=True))
    totals = {
        name: summary.total(name)
        for name in ("matches", "matches_scored", "rows", "quotes_skipped", "inserted", "updated", "deleted")
    }
    print(json.dumps({"totals": totals}, sort_keys=True))


async def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.football.scoring.cli", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    market = sub.add_parser("market", help="Score the bookmakers (default: 1617 to the current season)")
    market.add_argument("--season", default=None, help="one season only, e.g. 2324")
    market.add_argument("--from-season", default=FIRST_SEASON)
    market.add_argument("--to-season", default=None, help="default: the current season")
    args = parser.parse_args(argv)

    seasons = seasons_for(args, season_for(datetime.now(timezone.utc).date()))
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    workflow_id = f"football-market-scores-{seasons[0]}-{seasons[-1]}"
    print(f"Starting {workflow_id}: seasons {seasons[0]}..{seasons[-1]}")
    _print_summary(await run_market(client, settings.temporal_task_queue, MarketScoreRequest(seasons=seasons), workflow_id))
    return 0


def main() -> None:
    sys.exit(asyncio.run(_main(sys.argv[1:])))


if __name__ == "__main__":
    main()
