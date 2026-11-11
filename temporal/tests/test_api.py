"""The HTTP surface the browser talks to.

The Temporal client and the run store are injected, so these tests exercise the
endpoints without a server or a database. What they check is the contract the
frontend depends on, and the one rule that matters: the API starts runs and
relays decisions — it never writes a decision itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import create_app

AGENT_YAML = """
name: demo
provider: fake
model: fake-model
system_prompt: You are a demo agent.
answer_schema:
  type: object
  properties:
    summary: { type: string }
  required: [summary]
  additionalProperties: false
tools:
  - name: list_entity_types
"""


class StubHandle:
    def __init__(self, signals: list) -> None:
        self.signals = signals

    async def signal(self, name, arg=None):
        self.signals.append((name, arg))


class StubTemporalClient:
    def __init__(self) -> None:
        self.started: list[dict] = []
        self.signals: list = []

    async def start_workflow(self, workflow, arg, *, id, task_queue, **kwargs):
        self.started.append({"workflow": workflow, "arg": arg, "id": id, "queue": task_queue})
        return StubHandle(self.signals)

    def get_workflow_handle(self, workflow_id: str):
        self.signals.append(("handle", workflow_id))
        return StubHandle(self.signals)


class StubStore:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.runs: dict[str, dict] = {}

    def create_run(self, run_id, agent, question, workflow_id):
        self.created.append(
            {"id": run_id, "agent": agent, "question": question, "workflow_id": workflow_id}
        )

    def get_run(self, run_id):
        return self.runs.get(run_id)


@pytest.fixture
def agents_dir(tmp_path: Path) -> Path:
    (tmp_path / "demo.yaml").write_text(AGENT_YAML)
    return tmp_path


@pytest.fixture
def temporal() -> StubTemporalClient:
    return StubTemporalClient()


@pytest.fixture
def store() -> StubStore:
    return StubStore()


@pytest.fixture
def client(agents_dir: Path, temporal: StubTemporalClient, store: StubStore) -> TestClient:
    app = create_app(
        temporal_client=lambda: temporal,
        store=store,
        agents_dir=agents_dir,
    )
    return TestClient(app)


# ── Starting a run ────────────────────────────────────────────────────────


def test_starting_a_run_returns_its_id(client: TestClient) -> None:
    response = client.post("/agent/runs", json={"agent": "demo", "question": "What is here?"})

    assert response.status_code == 202
    body = response.json()
    assert body["run_id"]
    assert body["workflow_id"] == f"agent-{body['run_id']}"


def test_starting_a_run_records_it_before_the_workflow_starts(
    client: TestClient, store: StubStore, temporal: StubTemporalClient
) -> None:
    client.post("/agent/runs", json={"agent": "demo", "question": "What is here?"})

    # The row must exist first: the workflow's very first act is to write to it.
    assert store.created[0]["agent"] == "demo"
    assert store.created[0]["question"] == "What is here?"
    assert temporal.started[0]["id"] == f"agent-{store.created[0]['id']}"


def test_an_unknown_agent_is_refused_before_a_workflow_is_started(
    client: TestClient, temporal: StubTemporalClient
) -> None:
    response = client.post("/agent/runs", json={"agent": "ghost", "question": "q"})

    assert response.status_code == 404
    assert "ghost" in response.json()["detail"]
    assert temporal.started == []


def test_an_empty_question_is_refused(client: TestClient) -> None:
    response = client.post("/agent/runs", json={"agent": "demo", "question": "   "})

    assert response.status_code == 422


# ── Relaying a decision ───────────────────────────────────────────────────


def test_a_decision_is_relayed_as_a_signal(
    client: TestClient, temporal: StubTemporalClient
) -> None:
    response = client.post(
        "/agent/runs/11111111-1111-1111-1111-111111111111/decisions",
        json={"tool_call_id": "toolu_1", "approved": True, "decided_by": "jeevan"},
    )

    assert response.status_code == 204
    signalled = [s for s in temporal.signals if s[0] != "handle"]
    assert len(signalled) == 1
    decision = signalled[0][1]
    assert decision.tool_call_id == "toolu_1"
    assert decision.approved is True
    assert decision.decided_by == "jeevan"


def test_the_api_does_not_record_the_decision_itself(
    client: TestClient, store: StubStore
) -> None:
    # The workflow records it after the signal is durably in history, so the
    # decision is written exactly once. The API must not write it too.
    client.post(
        "/agent/runs/11111111-1111-1111-1111-111111111111/decisions",
        json={"tool_call_id": "toolu_1", "approved": False},
    )

    assert store.created == []


# ── Reading a run ─────────────────────────────────────────────────────────


def test_a_run_can_be_read_back(client: TestClient, store: StubStore) -> None:
    store.runs["abc"] = {"id": "abc", "status": "completed", "answer": {"summary": "hi"}}

    response = client.get("/agent/runs/abc")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_an_unknown_run_is_a_404(client: TestClient) -> None:
    assert client.get("/agent/runs/missing").status_code == 404


# ── Browser access ────────────────────────────────────────────────────────


def test_the_frontend_origin_is_allowed(client: TestClient) -> None:
    response = client.options(
        "/agent/runs",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_agents_can_be_listed_so_the_ui_need_not_hardcode_them(client: TestClient) -> None:
    response = client.get("/agent/agents")

    assert response.status_code == 200
    assert "demo" in response.json()["agents"]
