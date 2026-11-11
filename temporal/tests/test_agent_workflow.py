"""The agent loop, end to end.

These run against the Temporal server and Supabase in the local compose stack,
with `provider: fake` standing in for a model. Nothing is mocked: the loop, the
allowlist, the approval signal and the persisted transcript are all real. That is
the point — a mocked loop proves the mock.

Skipped automatically when Temporal is unreachable, so `pytest` still passes on a
machine with nothing running.
"""

from __future__ import annotations

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from temporalio.client import Client
from temporalio.worker import Worker

from src.activities import agent as agent_activities
from src.agent.contracts import AgentRunRequest, ToolDecision
from src.agent.registry import Registry
from src.config import settings
from src.workflows.agent.agent_workflow import AgentWorkflow

pytestmark = pytest.mark.asyncio

SCHEMA_NO_ARGS = {"type": "object", "properties": {}, "additionalProperties": False}

AGENT_YAML = """
name: test_agent
provider: fake
model: fake-model
max_tool_rounds: {rounds}
system_prompt: You are a test agent.
answer_schema:
  type: object
  properties:
    summary: {{ type: string }}
    findings: {{ type: array, items: {{ type: string }} }}
  required: [summary, findings]
  additionalProperties: false
tools:
{tools}
"""


@pytest.fixture
async def client() -> Client:
    try:
        return await asyncio.wait_for(
            Client.connect(settings.temporal_address, namespace=settings.temporal_namespace),
            timeout=5,
        )
    except Exception:  # noqa: BLE001
        pytest.skip(f"Temporal not reachable at {settings.temporal_address}")


@pytest.fixture
def registry() -> Registry:
    """Two read tools and one write tool, so the loop has something real to call."""
    reg = Registry()
    calls: list[str] = []

    @reg.tool(name="read_alpha", description="Read alpha.", input_schema=SCHEMA_NO_ARGS)
    def read_alpha() -> dict:
        calls.append("read_alpha")
        return {"alpha": 1}

    @reg.tool(
        name="write_beta",
        description="Write beta.",
        input_schema=SCHEMA_NO_ARGS,
        writes=True,
    )
    def write_beta() -> dict:
        calls.append("write_beta")
        return {"written": True}

    reg.calls = calls  # type: ignore[attr-defined]
    return reg


def write_agent(tmp_path: Path, tools: str, rounds: int = 4) -> Path:
    (tmp_path / "test_agent.yaml").write_text(AGENT_YAML.format(tools=tools, rounds=rounds))
    return tmp_path


async def run_agent(
    client: Client,
    *,
    agents_dir: Path,
    registry: Registry,
    question: str = "What is going on?",
    agent_name: str = "test_agent",
    approve: bool | None = None,
    persisted: list | None = None,
):
    """Start the workflow on its own queue with a worker wired to the test doubles."""
    task_queue = f"agent-test-{uuid.uuid4()}"
    run_id = str(uuid.uuid4())
    recorded = persisted if persisted is not None else []

    activities = agent_activities.build_activities(
        agents_dir=agents_dir,
        registry=registry,
        persistence=_RecordingPersistence(recorded),
    )

    # Activities here are synchronous (blocking HTTP and file reads), so they
    # need a thread pool, exactly as src/worker.py gives the real ones.
    async with Worker(
        client,
        task_queue=task_queue,
        workflows=[AgentWorkflow],
        activities=activities,
        activity_executor=ThreadPoolExecutor(max_workers=4),
        max_concurrent_activities=4,
    ):
        handle = await client.start_workflow(
            AgentWorkflow.run,
            AgentRunRequest(run_id=run_id, agent=agent_name, question=question),
            id=f"agent-{run_id}",
            task_queue=task_queue,
        )

        if approve is not None:
            pending = await _await_pending_approval(handle)
            await handle.signal(
                AgentWorkflow.submit_tool_decision,
                ToolDecision(
                    tool_call_id=pending["tool_call_id"],
                    approved=approve,
                    decided_by="tester",
                ),
            )

        return await asyncio.wait_for(handle.result(), timeout=30), recorded


async def _await_pending_approval(handle) -> dict:
    for _ in range(100):
        pending = await handle.query(AgentWorkflow.get_pending_approval)
        if pending:
            return pending
        await asyncio.sleep(0.1)
    raise AssertionError("no approval was requested")


class _RecordingPersistence:
    """Stands in for Supabase writes; records what the loop asked to persist."""

    def __init__(self, sink: list) -> None:
        self.sink = sink

    def persist_step(self, args) -> None:
        self.sink.append(("step", args.seq, args.role, args.tool_name))

    def record_pending_approval(self, args) -> None:
        self.sink.append(("pending", args.tool_call_id, args.tool_name))

    def record_decision(self, args) -> None:
        self.sink.append(("decision", args.tool_call_id, args.approved))

    def finalise_run(self, args) -> None:
        self.sink.append(("final", args.status, args.error))


# ── The loop ──────────────────────────────────────────────────────────────


async def test_a_read_only_run_answers_and_records_its_transcript(
    client: Client, registry: Registry, tmp_path: Path
) -> None:
    agents_dir = write_agent(tmp_path, "  - name: read_alpha\n")

    answer, recorded = await run_agent(client, agents_dir=agents_dir, registry=registry)

    assert registry.calls == ["read_alpha"]  # type: ignore[attr-defined]
    assert set(answer["answer"]) == {"summary", "findings"}
    assert answer["status"] == "completed"
    assert ("final", "completed", None) in recorded
    # user question, assistant turn, tool result, assistant answer
    assert [r for r in recorded if r[0] == "step"][0][1:] == (0, "user", None)


async def test_tool_results_are_fed_back_to_the_model(
    client: Client, registry: Registry, tmp_path: Path
) -> None:
    agents_dir = write_agent(tmp_path, "  - name: read_alpha\n")

    _, recorded = await run_agent(client, agents_dir=agents_dir, registry=registry)

    roles = [r[2] for r in recorded if r[0] == "step"]
    assert "tool" in roles


async def test_a_tool_outside_the_allowlist_is_never_dispatched(
    client: Client, registry: Registry, tmp_path: Path
) -> None:
    # The agent grants only read_alpha; the registry also holds write_beta, and
    # the loop must refuse it even if a model asks.
    agents_dir = write_agent(tmp_path, "  - name: read_alpha\n")

    await run_agent(client, agents_dir=agents_dir, registry=registry)

    assert "write_beta" not in registry.calls  # type: ignore[attr-defined]


# ── Human in the loop ─────────────────────────────────────────────────────


async def test_an_approved_write_tool_runs_after_the_signal(
    client: Client, registry: Registry, tmp_path: Path
) -> None:
    agents_dir = write_agent(
        tmp_path, "  - name: write_beta\n    requires_approval: true\n"
    )

    answer, recorded = await run_agent(
        client, agents_dir=agents_dir, registry=registry, approve=True
    )

    assert registry.calls == ["write_beta"]  # type: ignore[attr-defined]
    assert answer["status"] == "completed"
    assert any(r[0] == "pending" and r[2] == "write_beta" for r in recorded)
    assert any(r[0] == "decision" and r[2] is True for r in recorded)


async def test_a_rejected_write_tool_is_not_run(
    client: Client, registry: Registry, tmp_path: Path
) -> None:
    agents_dir = write_agent(
        tmp_path, "  - name: write_beta\n    requires_approval: true\n"
    )

    answer, recorded = await run_agent(
        client, agents_dir=agents_dir, registry=registry, approve=False
    )

    assert registry.calls == []  # type: ignore[attr-defined]
    assert answer["status"] == "completed"
    assert any(r[0] == "decision" and r[2] is False for r in recorded)


# ── Limits ────────────────────────────────────────────────────────────────


async def test_the_final_round_produces_an_answer_without_tools(
    client: Client, registry: Registry, tmp_path: Path
) -> None:
    # One round means: one model turn with tools, then the forced answer.
    agents_dir = write_agent(tmp_path, "  - name: read_alpha\n", rounds=1)

    answer, _ = await run_agent(client, agents_dir=agents_dir, registry=registry)

    assert answer["status"] == "completed"
    assert set(answer["answer"]) == {"summary", "findings"}


def failure_chain(error: BaseException) -> str:
    """Temporal wraps an activity failure, so the reason is in the cause chain."""
    messages, current = [], error
    while current is not None:
        messages.append(str(current))
        current = current.__cause__
    return " | ".join(messages)


async def test_an_unknown_agent_fails_the_run_and_records_why(
    client: Client, registry: Registry, tmp_path: Path
) -> None:
    write_agent(tmp_path, "  - name: read_alpha\n")
    recorded: list = []

    with pytest.raises(Exception) as raised:
        await run_agent(
            client,
            agents_dir=tmp_path,
            registry=registry,
            agent_name="ghost_agent",
            persisted=recorded,
        )

    assert "ghost_agent" in failure_chain(raised.value)
    # The browser must see a reason, not a run stuck on 'running'.
    assert [r for r in recorded if r[0] == "final"][0][1] == "failed"
