"""The agent loop.

Read the loop in `run` and you have read the whole design: ask the model, check
what it asked for against the allowlist, get a human decision when the tool
writes, dispatch, feed results back, repeat. Everything non-deterministic is an
activity, so a run replays exactly and survives a worker restart mid-approval.

WHAT MAY NOT GO IN THIS FILE: a model SDK, an HTTP call, a file read,
`datetime.now()`, `random`, `uuid4`, or a tool implementation. Temporal replays
this code, so anything that could answer differently on replay must be an
activity. Activities are referenced BY NAME so a caller can inject different
implementations — the worker injects Supabase-backed ones, tests inject
recorders, and this file does not change either way.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from ...agent.contracts import (
        AgentRunRequest,
        AgentSpec,
        DecisionArgs,
        FinaliseArgs,
        LlmDecideArgs,
        PendingApprovalArgs,
        PersistStepArgs,
        RunToolArgs,
        ToolCallSpec,
        ToolDecision,
        ToolOutcome,
        TurnResult,
    )

# A model call is slow and occasionally flaky; a config error inside it is raised
# non-retryable by the activity, so a bounded retry here only covers blips.
LLM_RETRY = RetryPolicy(maximum_attempts=3)
PERSIST_TIMEOUT = timedelta(seconds=30)
LLM_TIMEOUT = timedelta(seconds=180)


@workflow.defn
class AgentWorkflow:
    def __init__(self) -> None:
        self._decisions: dict[str, ToolDecision] = {}
        self._pending: dict[str, Any] | None = None
        self._status = "running"
        self._seq = 0

    # ── The loop ──────────────────────────────────────────────────────────

    @workflow.run
    async def run(self, request: AgentRunRequest) -> dict[str, Any]:
        try:
            spec: AgentSpec = await workflow.execute_activity(
                "agent_load_spec",
                request.agent,
                start_to_close_timeout=timedelta(seconds=30),
                result_type=AgentSpec,
            )

            transcript: list[dict[str, Any]] = [
                {"role": "user", "content": request.question}
            ]
            await self._persist(request.run_id, "user", request.question)

            for round_index in range(spec.max_tool_rounds + 1):
                # The last round withholds tools, so an agent that keeps calling
                # them still produces an answer instead of ending empty-handed.
                final_round = round_index == spec.max_tool_rounds

                turn: TurnResult = await workflow.execute_activity(
                    "agent_llm_decide",
                    LlmDecideArgs(
                        spec=spec, messages=transcript, tools_enabled=not final_round
                    ),
                    start_to_close_timeout=LLM_TIMEOUT,
                    retry_policy=LLM_RETRY,
                    result_type=TurnResult,
                )
                transcript.append({"role": "assistant", "content": turn.raw})
                await self._persist(
                    request.run_id,
                    "assistant",
                    turn.raw,
                    tokens_in=turn.tokens_in,
                    tokens_out=turn.tokens_out,
                )

                if turn.refused:
                    return await self._fail(
                        request.run_id,
                        "The model declined to answer this request.",
                    )

                if turn.final:
                    return await self._complete(request.run_id, turn)

                results: list[dict[str, Any]] = []
                for call in turn.tool_calls:
                    outcome = await self._handle_tool_call(request.run_id, spec, call, round_index)
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": outcome.tool_call_id,
                            "content": outcome.content,
                            "is_error": outcome.is_error,
                        }
                    )
                    await self._persist(
                        request.run_id, "tool", outcome.content, tool_name=outcome.tool_name
                    )

                transcript.append({"role": "user", "content": results})

            # Unreachable: the final round always returns. Kept as a guard so a
            # future edit to the loop cannot silently fall out of it.
            return await self._fail(request.run_id, "The agent loop ended without an answer.")

        except Exception as exc:
            # Record why the run died before letting Temporal see the failure,
            # so the browser shows a reason rather than a run stuck on 'running'.
            await workflow.execute_activity(
                "agent_finalise_run",
                FinaliseArgs(run_id=request.run_id, status="failed", error=str(exc)),
                start_to_close_timeout=PERSIST_TIMEOUT,
            )
            raise

    # ── One tool call ─────────────────────────────────────────────────────

    async def _handle_tool_call(
        self, run_id: str, spec: AgentSpec, call: ToolCallSpec, round_index: int
    ) -> ToolOutcome:
        # THE PRIVILEGE BOUNDARY. Checked here, above dispatch, so no code path
        # reaches a tool without passing it. A refusal is returned to the model
        # as a result rather than failing the run: the model can choose again,
        # and the attempt stays in the transcript.
        if call.name not in spec.allowed_tools:
            return ToolOutcome(
                tool_call_id=call.id,
                tool_name=call.name,
                content=(
                    f'Tool "{call.name}" is not permitted for agent "{spec.name}". '
                    f'Permitted tools: {", ".join(sorted(spec.allowed_tools))}'
                ),
                is_error=True,
            )

        if spec.requires_approval(call.name):
            decision = await self._await_decision(run_id, call)
            if not decision.approved:
                return ToolOutcome(
                    tool_call_id=call.id,
                    tool_name=call.name,
                    content=f"A human reviewer rejected this call to {call.name}.",
                    is_error=True,
                )

        return await workflow.execute_activity(
            "agent_run_tool",
            RunToolArgs(
                tool_call_id=call.id, tool_name=call.name, arguments=call.arguments
            ),
            # Names the tool in Temporal history, so the timeline reads as the
            # agent's actual steps rather than a column of "agent_run_tool".
            activity_id=f"{call.name}-{round_index}-{call.id[:8]}",
            start_to_close_timeout=timedelta(seconds=spec.timeout_seconds_for(call.name)),
            result_type=ToolOutcome,
        )

    async def _await_decision(self, run_id: str, call: ToolCallSpec) -> ToolDecision:
        await workflow.execute_activity(
            "agent_record_pending_approval",
            PendingApprovalArgs(
                run_id=run_id,
                tool_call_id=call.id,
                tool_name=call.name,
                arguments=call.arguments,
            ),
            start_to_close_timeout=PERSIST_TIMEOUT,
        )
        self._status = "awaiting_approval"
        self._pending = {
            "tool_call_id": call.id,
            "tool_name": call.name,
            "arguments": call.arguments,
        }

        # No timeout: a run may legitimately wait days for a human, and Temporal
        # holds it for free. Add a timer here if a deployment wants runs to expire.
        await workflow.wait_condition(lambda: call.id in self._decisions)

        decision = self._decisions[call.id]
        self._status = "running"
        self._pending = None
        await workflow.execute_activity(
            "agent_record_decision",
            DecisionArgs(
                run_id=run_id,
                tool_call_id=call.id,
                approved=decision.approved,
                decided_by=decision.decided_by,
            ),
            start_to_close_timeout=PERSIST_TIMEOUT,
        )
        return decision

    # ── Endings ───────────────────────────────────────────────────────────

    async def _complete(self, run_id: str, turn: TurnResult) -> dict[str, Any]:
        # A model that answered in prose instead of the schema still answered;
        # the words are kept rather than discarded for failing to be JSON.
        answer = turn.answer if turn.answer is not None else {"summary": turn.text or ""}
        self._status = "completed"
        await workflow.execute_activity(
            "agent_finalise_run",
            FinaliseArgs(run_id=run_id, status="completed", answer=answer),
            start_to_close_timeout=PERSIST_TIMEOUT,
        )
        return {
            "status": "completed",
            "answer": answer,
            "schema_satisfied": turn.answer is not None,
        }

    async def _fail(self, run_id: str, error: str) -> dict[str, Any]:
        self._status = "failed"
        await workflow.execute_activity(
            "agent_finalise_run",
            FinaliseArgs(run_id=run_id, status="failed", error=error),
            start_to_close_timeout=PERSIST_TIMEOUT,
        )
        return {"status": "failed", "answer": None, "error": error}

    async def _persist(
        self,
        run_id: str,
        role: str,
        content: Any,
        *,
        tool_name: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> None:
        await workflow.execute_activity(
            "agent_persist_step",
            PersistStepArgs(
                run_id=run_id,
                seq=self._seq,
                role=role,
                content=content,
                tool_name=tool_name,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            ),
            start_to_close_timeout=PERSIST_TIMEOUT,
        )
        self._seq += 1

    # ── Outside world ─────────────────────────────────────────────────────

    @workflow.signal
    def submit_tool_decision(self, decision: ToolDecision) -> None:
        self._decisions[decision.tool_call_id] = decision

    @workflow.query
    def get_status(self) -> str:
        return self._status

    @workflow.query
    def get_pending_approval(self) -> dict[str, Any] | None:
        return self._pending
