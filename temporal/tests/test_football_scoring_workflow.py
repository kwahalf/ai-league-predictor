"""Orchestration tests for ``MarketScoreWorkflow`` in Temporal's time-skipping test environment.

The real activity is replaced by a recorder registered under the same name. The test server is
downloaded on first use; without network the tests skip.
"""

from __future__ import annotations

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.football.scoring.models import MarketScoreRequest, MarketScoreResult, MarketScoreSummary, SeasonRef
from src.football.scoring.workflow import MarketScoreWorkflow


@pytest.fixture
async def env():
    try:
        environment = await asyncio.wait_for(WorkflowEnvironment.start_time_skipping(), timeout=120)
    except Exception as exc:  # noqa: BLE001 - no network to fetch the test server, etc.
        pytest.skip(f"Temporal test environment unavailable: {exc}")
    try:
        yield environment
    finally:
        await environment.shutdown()


class Recorder:
    def __init__(self) -> None:
        self.calls: list[SeasonRef] = []

    def activities(self) -> list:
        @activity.defn(name="football_score_market")
        def score(ref: SeasonRef) -> MarketScoreResult:
            self.calls.append(ref)
            return MarketScoreResult(season=ref.season, matches=380, rows=3800, inserted=3800,
                                     mean_rps={"consensus/pre_match": 0.19})

        return [score]


async def _run(env: WorkflowEnvironment, recorder: Recorder, request: MarketScoreRequest) -> MarketScoreSummary:
    queue = f"scoring-test-{uuid.uuid4()}"
    async with Worker(
        env.client,
        task_queue=queue,
        workflows=[MarketScoreWorkflow],
        activities=recorder.activities(),
        activity_executor=ThreadPoolExecutor(max_workers=2),
        max_concurrent_activities=2,
    ):
        return await env.client.execute_workflow(
            MarketScoreWorkflow.run, request, id=f"scoring-{uuid.uuid4()}", task_queue=queue,
            execution_timeout=timedelta(hours=1),
        )


async def test_scores_each_season_in_order(env):
    recorder = Recorder()
    summary = await _run(env, recorder, MarketScoreRequest(seasons=["1617", "2324"]))
    assert [(c.season, c.division) for c in recorder.calls] == [("1617", "E0"), ("2324", "E0")]
    assert summary.total("inserted") == 7600
    assert summary.seasons[1].mean_rps == {"consensus/pre_match": 0.19}


async def test_default_request_is_the_current_season(env):
    recorder = Recorder()
    summary = await _run(env, recorder, MarketScoreRequest())
    assert len(recorder.calls) == 1 and len(summary.seasons) == 1


async def test_an_invalid_season_fails_before_any_activity(env):
    recorder = Recorder()
    with pytest.raises(WorkflowFailureError):
        await _run(env, recorder, MarketScoreRequest(seasons=["2016"]))
    assert recorder.calls == []
