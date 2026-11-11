"""Orchestration tests for ``IngestWorkflow`` in Temporal's time-skipping test environment.

The real activities are replaced by recorders registered under the same names, so these tests
check only what the workflow decides: which files, in which order, with which inputs, and how
failures surface. The test server is downloaded on first use; without network the tests skip.
"""

from __future__ import annotations

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.football.ingest.models import CsvSource, IngestRequest, IngestSummary, LoadResult, RawCsvRef
from src.football.ingest.workflow import IngestWorkflow
from src.football.quality.models import QualityReport, QualityRequest


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
    def __init__(self, fail_load_for: str | None = None, flaky_fetches: int = 0) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.fail_load_for = fail_load_for
        self.flaky_fetches = flaky_fetches

    def activities(self) -> list:
        @activity.defn(name="football_fetch_csv")
        def fetch(source: CsvSource) -> RawCsvRef:
            if self.flaky_fetches:
                self.flaky_fetches -= 1
                raise ApplicationError("HTTP 503", type="DownloadError")
            self.calls.append(("fetch", source.kind, source.season))
            return RawCsvRef(
                kind=source.kind, season=source.season, division=source.division,
                url=f"u/{source.kind}/{source.season}", storage_path=f"p/{source.kind}/{source.season}",
                sha256="0" * 64, size_bytes=1, downloaded_at="2026-11-11T06:00:00+00:00",
            )

        @activity.defn(name="football_load_csv")
        def load(ref: RawCsvRef) -> LoadResult:
            self.calls.append(("load", ref.kind, ref.season))
            if ref.season == self.fail_load_for:
                raise ApplicationError("bad file", type="CsvFormatError", non_retryable=True)
            return LoadResult(
                kind=ref.kind, season=ref.season, storage_path=ref.storage_path,
                matches_in_file=380, matches_inserted=380, prices_dropped=1 if ref.season == "2122" else 0,
            )

        # The data-quality check (1.4) runs last; it has its own tests (test_football_quality*.py).
        @activity.defn(name="football_quality_check")
        def quality(request: QualityRequest) -> QualityReport:
            self.calls.append(("quality", request.division, request.current_season))
            return QualityReport(division=request.division, current_season=request.current_season)

        return [fetch, load, quality]


async def _run(env: WorkflowEnvironment, recorder: Recorder, request: IngestRequest) -> IngestSummary:
    queue = f"ingest-test-{uuid.uuid4()}"
    async with Worker(
        env.client,
        task_queue=queue,
        workflows=[IngestWorkflow],
        activities=recorder.activities(),
        activity_executor=ThreadPoolExecutor(max_workers=2),
        max_concurrent_activities=2,
    ):
        return await env.client.execute_workflow(
            IngestWorkflow.run, request, id=f"ingest-{uuid.uuid4()}", task_queue=queue,
            execution_timeout=timedelta(hours=2),
        )


async def test_backfill_loads_each_season_in_order_then_fixtures(env):
    recorder = Recorder()
    seasons = ["1617", "2122", "2627"]

    summary = await _run(env, recorder, IngestRequest(seasons=seasons))

    assert recorder.calls == [
        ("fetch", "season", "1617"), ("load", "season", "1617"),
        ("fetch", "season", "2122"), ("load", "season", "2122"),
        ("fetch", "season", "2627"), ("load", "season", "2627"),
        ("fetch", "fixtures", summary.files[-1].season), ("load", "fixtures", summary.files[-1].season),
        ("quality", "E0", summary.files[-1].season),
    ]
    assert [f.season for f in summary.files[:3]] == seasons
    assert summary.total("matches_inserted") == 4 * 380
    assert summary.total("prices_dropped") == 1


async def test_default_request_is_the_current_season_and_fixtures(env):
    recorder = Recorder()
    summary = await _run(env, recorder, IngestRequest())
    # The test server's clock is real time, so the season is whatever is current now.
    kinds = [(c[1], c[2]) for c in recorder.calls if c[0] == "fetch"]
    assert recorder.calls[-1] == ("quality", "E0", kinds[0][1])
    assert len(kinds) == 2
    assert kinds[0][0] == "season" and kinds[1] == ("fixtures", kinds[0][1])
    assert len(summary.files) == 2


async def test_fixtures_can_be_left_out(env):
    recorder = Recorder()
    await _run(env, recorder, IngestRequest(seasons=["2324"], include_fixtures=False))
    assert recorder.calls[:2] == [("fetch", "season", "2324"), ("load", "season", "2324")]
    assert [c[0] for c in recorder.calls[2:]] == ["quality"]


async def test_a_flaky_download_is_retried(env):
    recorder = Recorder(flaky_fetches=2)
    summary = await _run(env, recorder, IngestRequest(seasons=["2324"], include_fixtures=False))
    assert len(summary.files) == 1


async def test_bad_data_fails_the_workflow_and_stops(env):
    recorder = Recorder(fail_load_for="1718")
    with pytest.raises(WorkflowFailureError):
        await _run(env, recorder, IngestRequest(seasons=["1617", "1718", "1819"]))
    assert ("fetch", "season", "1819") not in recorder.calls
    assert ("load", "fixtures", "2627") not in recorder.calls
    assert not any(c[0] == "quality" for c in recorder.calls)


async def test_an_invalid_season_fails_before_any_activity(env):
    recorder = Recorder()
    with pytest.raises(WorkflowFailureError):
        await _run(env, recorder, IngestRequest(seasons=["2016"]))
    assert recorder.calls == []
