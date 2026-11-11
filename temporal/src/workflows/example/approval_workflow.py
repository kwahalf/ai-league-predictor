from __future__ import annotations
import asyncio
import datetime
from dataclasses import asdict, dataclass
from typing import Any, Optional
from temporalio import workflow


@dataclass
class ApprovalRequest:
    entity_id: str
    entity_type: str
    requested_by: str
    approvers: list[str]
    timeout_hours: int = 24


@dataclass
class ApprovalDecision:
    approved: bool
    decided_by: str
    comments: Optional[str] = None


@workflow.defn
class ApprovalWorkflow:
    def __init__(self) -> None:
        self._status = "pending_approval"
        self._decision: ApprovalDecision | None = None

    @workflow.run
    async def run(self, request: ApprovalRequest) -> dict[str, Any]:
        # timeout expects a duration (timedelta/seconds), not an absolute datetime.
        # wait_condition raises TimeoutError when the deadline passes.
        try:
            await workflow.wait_condition(
                lambda: self._decision is not None,
                timeout=datetime.timedelta(hours=request.timeout_hours),
            )
        except asyncio.TimeoutError:
            pass

        if self._decision is None:
            # timed out
            self._status = "rejected"
            self._decision = ApprovalDecision(approved=False, decided_by="system", comments="Timed out")
        else:
            self._status = "approved" if self._decision.approved else "rejected"

        return {
            "entity_id": request.entity_id,
            "status": self._status,
            "decision": asdict(self._decision) if self._decision else None,
        }

    @workflow.signal
    async def submit_decision(self, decision: ApprovalDecision):
        self._decision = decision

    @workflow.query
    def get_status(self) -> str:
        return self._status
