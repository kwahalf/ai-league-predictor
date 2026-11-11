"""The HTTP surface for agent runs.

Small on purpose. It does three things: start a run, relay a human decision to a
running workflow, and read a run back. Everything else the browser needs it reads
straight from Supabase, which it can already do.

ONE RULE WORTH STATING: THIS SERVICE DOES NOT RECORD DECISIONS. It signals the
workflow, and the workflow records the outcome after the signal is durably in
history. Writing it here as well would mean two writers for one fact, and a
crash between them would leave the database claiming something the run never saw.

Runs on the worker image as a separate compose service:

    uvicorn src.api:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Callable, Protocol

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from temporalio.client import Client

from .activities.client import request
from .agent import tools as _agent_tools  # noqa: F401 — importing registers the tools
from .agent.config import DEFAULT_AGENTS_DIR, ConfigError, load_agent_config
from .agent.contracts import AgentRunRequest, ToolDecision
from .config import settings
from .workflows.agent.agent_workflow import AgentWorkflow

# Local development serves the frontend from Vite on 3000. A deployment narrows
# this to its own origin; a wildcard here would be a standing invitation.
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "AGENT_API_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
    if origin.strip()
]


class StartRunBody(BaseModel):
    agent: str = Field(min_length=1)
    question: str = Field(min_length=1)

    @field_validator("agent", "question")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class DecisionBody(BaseModel):
    tool_call_id: str = Field(min_length=1)
    approved: bool
    decided_by: str = "unknown"


class RunStore(Protocol):
    def create_run(self, run_id: str, agent: str, question: str, workflow_id: str) -> None: ...
    def get_run(self, run_id: str) -> dict[str, Any] | None: ...


class SupabaseRunStore:
    """The run row, written with the service-role key before the workflow starts."""

    def create_run(self, run_id: str, agent: str, question: str, workflow_id: str) -> None:
        request(
            "agent_runs",
            {
                "id": run_id,
                "agent": agent,
                "question": question,
                "workflow_id": workflow_id,
                "status": "running",
            },
            prefer="return=minimal",
        )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        rows = request(f"agent_runs?id=eq.{run_id}&select=*", method="GET") or []
        return rows[0] if rows else None


def create_app(
    *,
    temporal_client: Callable[[], Any] | None = None,
    store: RunStore | None = None,
    agents_dir: Path | str | None = None,
) -> FastAPI:
    """Build the app with its dependencies injected, so tests need no server."""
    app = FastAPI(title="Agent API", version="1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    run_store = store if store is not None else SupabaseRunStore()
    directory = Path(agents_dir) if agents_dir else DEFAULT_AGENTS_DIR

    async def get_client() -> Any:
        if temporal_client is not None:
            return temporal_client()
        return await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )

    @app.get("/agent/agents")
    def list_agents() -> dict[str, list[str]]:
        """Which agents this deployment has, so the UI need not hardcode them."""
        if not directory.is_dir():
            return {"agents": []}
        return {"agents": sorted(p.stem for p in directory.glob("*.yaml"))}

    @app.post("/agent/runs", status_code=202)
    async def start_run(body: StartRunBody) -> dict[str, str]:
        # Validated here so a typo is a 404 the user can read, rather than a
        # workflow that starts and immediately fails.
        try:
            load_agent_config(body.agent, agents_dir=directory)
        except ConfigError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        run_id = str(uuid.uuid4())
        workflow_id = f"agent-{run_id}"

        # The row first: the workflow's first act is to write a transcript entry
        # against it, and a message with no run would be an orphan.
        run_store.create_run(run_id, body.agent, body.question, workflow_id)

        client = await get_client()
        await client.start_workflow(
            AgentWorkflow.run,
            AgentRunRequest(run_id=run_id, agent=body.agent, question=body.question),
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
        )
        return {"run_id": run_id, "workflow_id": workflow_id}

    @app.post("/agent/runs/{run_id}/decisions", status_code=204)
    async def decide(run_id: str, body: DecisionBody) -> None:
        client = await get_client()
        handle = client.get_workflow_handle(f"agent-{run_id}")
        try:
            await handle.signal(
                AgentWorkflow.submit_tool_decision,
                ToolDecision(
                    tool_call_id=body.tool_call_id,
                    approved=body.approved,
                    decided_by=body.decided_by,
                ),
            )
        except Exception as exc:  # noqa: BLE001 — a finished or unknown run is a 404
            raise HTTPException(
                status_code=404,
                detail=f"No running agent workflow for run {run_id}: {exc}",
            ) from exc

    @app.get("/agent/runs/{run_id}")
    def read_run(run_id: str) -> dict[str, Any]:
        run = run_store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"No agent run {run_id}")
        return run

    return app


app = create_app()
