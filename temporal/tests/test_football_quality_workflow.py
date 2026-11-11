"""How ``IngestWorkflow`` runs the data-quality check (plan step 1.4), in Temporal's time-skipping
test environment.

The ingest activities are fakes that record calls; the quality activity is the real one, built with
a fake gateway, so the real rules and the real error decide the outcome. The test server is
downloaded on first use; without network the tests skip.
"""

from __future__ import annotations

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.football.ingest.models import CsvSource, IngestRequest, IngestSummary, LoadResult, RawCsvRef
from src.football.ingest.workflow import IngestWorkflow
from src.football.quality.activities import build_activities as build_quality_activities
from test_football_quality import good_facts


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


class QualityGateway:
    """Answers the quality RPC; ``outages`` first calls fail like a restarting database."""

    def __init__(self, reply: dict, outages: int = 0) -> None:
        self.reply = reply
        self.outages = outages
        self.calls: list[dict] = []

    def rpc(self, function: str, args: dict) -> Any:
        if self.outages:
            self.outages -= 1
            raise ApplicationError(f"Supabase RPC {function} failed (HTTP 503)", type="RpcError")
        self.calls.append(args)
        return self.reply


def _ingest_fakes(loads: list[str]) -> list:
    @activity.defn(name="football_fetch_csv")
    def fetch(source: CsvSource) -> RawCsvRef:
        return RawCsvRef(
            kind=source.kind, season=source.season, division=source.division, url="u", storage_path="p",
            sha256="0" * 64, size_bytes=1, downloaded_at="2026-11-11T06:00:00+00:00",
        )

    @activity.defn(name="football_load_csv")
    def load(ref: RawCsvRef) -> LoadResult:
        loads.append(ref.season)
        return LoadResult(kind=ref.kind, season=ref.season, storage_path=ref.storage_path, matches_inserted=1)

    return [fetch, load]


async def _run(env: WorkflowEnvironment, gateway: QualityGateway, loads: list[str]) -> IngestSummary:
    queue = f"quality-test-{uuid.uuid4()}"
    async with Worker(
        env.client,
        task_queue=queue,
        workflows=[IngestWorkflow],
        activities=[*_ingest_fakes(loads), *build_quality_activities(gateway)],
        activity_executor=ThreadPoolExecutor(max_workers=2),
        max_concurrent_activities=2,
    ):
        return await env.client.execute_workflow(
            IngestWorkflow.run, IngestRequest(seasons=["2526"]), id=f"ingest-{uuid.uuid4()}", task_queue=queue,
            execution_timeout=timedelta(hours=2),
        )


async def test_clean_data_completes_the_run_after_the_loads(env):
    loads: list[str] = []
    gateway = QualityGateway(good_facts())
    summary = await _run(env, gateway, loads)
    assert len(summary.files) == 2 and len(loads) == 2
    assert len(gateway.calls) == 1


async def test_a_failed_check_fails_the_run_with_the_checks_message(env):
    facts = good_facts()
    facts["seasons"][0]["matches"] = 379
    loads: list[str] = []
    gateway = QualityGateway(facts)
    with pytest.raises(WorkflowFailureError) as info:
        await _run(env, gateway, loads)
    failure = info.value.cause
    # The run's own failure carries the message, so the Temporal UI shows it on the run.
    assert isinstance(failure, ApplicationError)
    assert failure.type == "DataQualityError"
    assert "completed_seasons: 1 seasons: season 1617: 379 matches, expected 380" in failure.message
    # Everything was loaded first, and a failed check is not retried.
    assert len(loads) == 2
    assert len(gateway.calls) == 1


async def test_a_database_outage_during_the_check_is_retried(env):
    loads: list[str] = []
    gateway = QualityGateway(good_facts(), outages=2)
    summary = await _run(env, gateway, loads)
    assert len(summary.files) == 2
    assert len(gateway.calls) == 1
