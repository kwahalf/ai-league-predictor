from __future__ import annotations
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from temporalio.client import Client
from temporalio.worker import Worker

from .config import settings
from .activities import supabase_core, notifications
from .activities.agent import build_activities as build_agent_activities
# Importing the tools module is what registers them, so the worker must import
# it even though it uses no name from it directly.
from .agent import tools as _agent_tools  # noqa: F401
from .workflows.agent.agent_workflow import AgentWorkflow
from .workflows.example.approval_workflow import ApprovalWorkflow
from .football.ingest.activities import build_activities as build_football_ingest_activities
from .football.ingest.workflow import IngestWorkflow
from .football.scoring.activities import build_activities as build_football_scoring_activities
from .football.scoring.workflow import MarketScoreWorkflow

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    logger.info("Connecting to Temporal", extra={"address": settings.temporal_address, "namespace": settings.temporal_namespace})
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)

    activity_executor = ThreadPoolExecutor(max_workers=20)
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[ApprovalWorkflow, AgentWorkflow, IngestWorkflow, MarketScoreWorkflow],
        activities=[
            supabase_core.create_entity,
            supabase_core.update_entity_scd2,
            supabase_core.get_entity,
            supabase_core.append_event,
            supabase_core.create_relationship,
            notifications.send_email,
            notifications.send_notification,
            # Agent activities are built rather than imported so the tool
            # registry and persistence are injected in one place.
            *build_agent_activities(),
            # Football ingest (plan step 1.3): download + store raw CSV, parse + upsert.
            *build_football_ingest_activities(),
            # Football market scores (plan step 2.1b): score the bookmakers per season.
            *build_football_scoring_activities(),
        ],
        activity_executor=activity_executor,
        max_concurrent_activities=20,
    )

    logger.info("Worker started", extra={"task_queue": settings.temporal_task_queue})
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
