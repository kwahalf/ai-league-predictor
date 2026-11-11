"""Agent activities — everything the loop is not allowed to do itself.

The workflow may not call a model, read a file, touch the network or execute a
tool: all of that is non-deterministic and belongs here. The workflow only
decides. That split is what makes a run replayable.

`build_activities` exists so the configuration directory, the tool registry and
the persistence layer are injected rather than imported. The worker injects the
real ones; tests inject their own and exercise the same code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Protocol

from temporalio import activity
from temporalio.exceptions import ApplicationError

from ..agent.config import ConfigError, load_agent_config
from ..agent.contracts import (
    AgentSpec,
    DecisionArgs,
    FinaliseArgs,
    LlmDecideArgs,
    PendingApprovalArgs,
    PersistStepArgs,
    RunToolArgs,
    ToolCallSpec,
    ToolGrantSpec,
    ToolOutcome,
    TurnResult,
)
from ..agent.registry import Registry, ToolNotFound, ToolValidationError
from ..agent.registry import registry as default_registry
from .client import request


class Persistence(Protocol):
    """Where a run's state is written. Implemented by Supabase here, by a
    recorder in tests, and by anything else a deployment prefers."""

    def persist_step(self, args: PersistStepArgs) -> None: ...
    def record_pending_approval(self, args: PendingApprovalArgs) -> None: ...
    def record_decision(self, args: DecisionArgs) -> None: ...
    def finalise_run(self, args: FinaliseArgs) -> None: ...


class SupabasePersistence:
    """Writes run state through PostgREST with the service-role key.

    Every write is idempotent: the transcript upserts on `(run_id, seq)` and the
    status writes are last-value-wins, so a retried activity cannot duplicate a
    transcript entry or corrupt a run.
    """

    def persist_step(self, args: PersistStepArgs) -> None:
        request(
            "agent_messages?on_conflict=run_id,seq",
            {
                "run_id": args.run_id,
                "seq": args.seq,
                "role": args.role,
                "content": args.content,
                "tool_name": args.tool_name,
                "tokens_in": args.tokens_in,
                "tokens_out": args.tokens_out,
            },
            prefer="resolution=ignore-duplicates,return=minimal",
        )

    def record_pending_approval(self, args: PendingApprovalArgs) -> None:
        request(
            "agent_approvals?on_conflict=run_id,tool_call_id",
            {
                "run_id": args.run_id,
                "tool_call_id": args.tool_call_id,
                "tool_name": args.tool_name,
                "arguments": args.arguments,
                "status": "pending",
            },
            prefer="resolution=ignore-duplicates,return=minimal",
        )
        self._set_status(args.run_id, "awaiting_approval")

    def record_decision(self, args: DecisionArgs) -> None:
        request(
            f"agent_approvals?run_id=eq.{args.run_id}&tool_call_id=eq.{args.tool_call_id}",
            {
                "status": "approved" if args.approved else "rejected",
                "decided_by": args.decided_by,
                "decided_at": "now()",
            },
            method="PATCH",
            prefer="return=minimal",
        )
        self._set_status(args.run_id, "running")

    def finalise_run(self, args: FinaliseArgs) -> None:
        request(
            f"agent_runs?id=eq.{args.run_id}",
            {"status": args.status, "answer": args.answer, "error": args.error},
            method="PATCH",
            prefer="return=minimal",
        )

    def _set_status(self, run_id: str, status: str) -> None:
        request(
            f"agent_runs?id=eq.{run_id}",
            {"status": status},
            method="PATCH",
            prefer="return=minimal",
        )


def build_activities(
    *,
    agents_dir: Path | str | None = None,
    registry: Registry | None = None,
    persistence: Persistence | None = None,
) -> list[Callable[..., Any]]:
    reg = registry if registry is not None else default_registry
    store = persistence if persistence is not None else SupabasePersistence()

    @activity.defn(name="agent_load_spec")
    def agent_load_spec(agent: str) -> AgentSpec:
        """Load and validate an agent config, flattened for the workflow.

        A bad configuration is a permanent failure, not a blip: retrying a typo
        forever would hide it, so it is raised non-retryable and names the agent.
        """
        try:
            cfg = load_agent_config(agent, agents_dir=agents_dir, registry=reg)
        except ConfigError as exc:
            raise ApplicationError(str(exc), non_retryable=True) from exc

        return AgentSpec(
            name=cfg.name,
            provider=cfg.provider,
            model=cfg.model,
            system_prompt=cfg.system_prompt,
            answer_schema=cfg.answer_schema,
            tools=[
                ToolGrantSpec(
                    name=grant.name,
                    requires_approval=cfg.requires_approval(grant.name),
                    timeout_seconds=cfg.timeout_seconds_for(grant.name),
                )
                for grant in cfg.tools
            ],
            max_tool_rounds=cfg.max_tool_rounds,
            max_tokens=cfg.max_tokens,
        )

    @activity.defn(name="agent_llm_decide")
    def agent_llm_decide(args: LlmDecideArgs) -> TurnResult:
        """One model turn. The only place a provider SDK is reached."""
        # Imported here rather than at module scope so the workflow bundle never
        # pulls a model SDK in, and so a missing optional provider only breaks
        # the provider that is actually configured.
        from ..agent.llm import UnknownProvider, call_model
        from ..agent.llm.claude import ClaudeConfigError
        from ..agent.llm.types import LlmRequest

        spec = args.spec
        try:
            turn = call_model(
                LlmRequest(
                    provider=spec.provider,
                    model=spec.model,
                    system_prompt=spec.system_prompt,
                    messages=args.messages,
                    tools=reg.declarations(sorted(spec.allowed_tools)),
                    answer_schema=spec.answer_schema,
                    max_tokens=spec.max_tokens,
                    tools_enabled=args.tools_enabled,
                )
            )
        except (UnknownProvider, ClaudeConfigError, ToolNotFound) as exc:
            # Misconfiguration: retrying cannot fix it.
            raise ApplicationError(str(exc), non_retryable=True) from exc

        activity.logger.info(
            "agent model turn",
            extra={
                "provider": spec.provider,
                "model": turn.model,
                "tool_calls": [c.name for c in turn.tool_calls],
                "final": turn.final,
            },
        )
        return TurnResult(
            raw=turn.raw,
            model=turn.model,
            tool_calls=[
                ToolCallSpec(id=c.id, name=c.name, arguments=c.arguments) for c in turn.tool_calls
            ],
            text=turn.text,
            answer=turn.answer,
            final=turn.final,
            refused=turn.refused,
            tokens_in=turn.usage.input_tokens,
            tokens_out=turn.usage.output_tokens,
        )

    @activity.defn(name="agent_run_tool")
    def agent_run_tool(args: RunToolArgs) -> ToolOutcome:
        """Execute one tool call.

        A tool that fails returns an error result rather than failing the run:
        the model can read the error and recover, and the failure stays in the
        transcript where a human can see it. Results are truncated so a chatty
        tool cannot push the transcript past Temporal's payload limits.
        """
        try:
            result = reg.dispatch(args.tool_name, args.arguments)
        except (ToolNotFound, ToolValidationError) as exc:
            return ToolOutcome(
                tool_call_id=args.tool_call_id,
                tool_name=args.tool_name,
                content=str(exc),
                is_error=True,
            )
        except Exception as exc:  # noqa: BLE001 — reported to the model, not swallowed
            activity.logger.warning(f"tool {args.tool_name} failed: {exc}")
            return ToolOutcome(
                tool_call_id=args.tool_call_id,
                tool_name=args.tool_name,
                content=f"Tool failed: {exc}",
                is_error=True,
            )

        return ToolOutcome(
            tool_call_id=args.tool_call_id,
            tool_name=args.tool_name,
            content=_truncate(result, args.max_result_chars),
        )

    @activity.defn(name="agent_persist_step")
    def agent_persist_step(args: PersistStepArgs) -> None:
        store.persist_step(args)

    @activity.defn(name="agent_record_pending_approval")
    def agent_record_pending_approval(args: PendingApprovalArgs) -> None:
        store.record_pending_approval(args)

    @activity.defn(name="agent_record_decision")
    def agent_record_decision(args: DecisionArgs) -> None:
        store.record_decision(args)

    @activity.defn(name="agent_finalise_run")
    def agent_finalise_run(args: FinaliseArgs) -> None:
        store.finalise_run(args)

    return [
        agent_load_spec,
        agent_llm_decide,
        agent_run_tool,
        agent_persist_step,
        agent_record_pending_approval,
        agent_record_decision,
        agent_finalise_run,
    ]


def _truncate(result: Any, limit: int) -> str:
    text = result if isinstance(result, str) else json.dumps(result, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + f"… [truncated, {len(text)} chars total]"
