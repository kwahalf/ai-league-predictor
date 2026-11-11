"""Dataclasses that cross the workflow/activity boundary.

Kept in their own module with no third-party imports so the workflow can import
them without dragging a model SDK, a YAML parser or an HTTP client into the
workflow sandbox. Everything here is plain data that Temporal can serialise into
history and replay.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentRunRequest:
    run_id: str
    agent: str
    question: str


@dataclass
class ToolDecision:
    tool_call_id: str
    approved: bool
    decided_by: str = "unknown"


@dataclass
class ToolGrantSpec:
    name: str
    requires_approval: bool = False
    timeout_seconds: int = 60


@dataclass
class AgentSpec:
    """A validated agent configuration, flattened for the workflow.

    The workflow needs the allowlist, the approval flags and the timeouts; the
    model call needs the prompt, schema and provider. Both travel together so a
    run's whole configuration is visible in Temporal history — which is what
    makes a past run auditable rather than merely repeated.
    """

    name: str
    provider: str
    model: str
    system_prompt: str
    answer_schema: dict[str, Any]
    tools: list[ToolGrantSpec] = field(default_factory=list)
    max_tool_rounds: int = 6
    max_tokens: int = 4096

    @property
    def allowed_tools(self) -> set[str]:
        return {t.name for t in self.tools}

    def requires_approval(self, tool_name: str) -> bool:
        return any(t.name == tool_name and t.requires_approval for t in self.tools)

    def timeout_seconds_for(self, tool_name: str) -> int:
        for grant in self.tools:
            if grant.name == tool_name:
                return grant.timeout_seconds
        return 60


@dataclass
class ToolCallSpec:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnResult:
    """One model turn, normalised. `raw` is replayed verbatim into the transcript."""

    raw: Any
    model: str
    tool_calls: list[ToolCallSpec] = field(default_factory=list)
    text: str | None = None
    answer: dict[str, Any] | None = None
    final: bool = False
    refused: bool = False
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass
class LlmDecideArgs:
    spec: AgentSpec
    messages: list[dict[str, Any]]
    tools_enabled: bool = True


@dataclass
class RunToolArgs:
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    max_result_chars: int = 8000


@dataclass
class ToolOutcome:
    """A tool result in the canonical transcript shape's terms."""

    tool_call_id: str
    tool_name: str
    content: str
    is_error: bool = False


@dataclass
class PersistStepArgs:
    run_id: str
    seq: int
    role: str
    content: Any
    tool_name: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass
class PendingApprovalArgs:
    run_id: str
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class DecisionArgs:
    run_id: str
    tool_call_id: str
    approved: bool
    decided_by: str = "unknown"


@dataclass
class FinaliseArgs:
    run_id: str
    status: str
    answer: dict[str, Any] | None = None
    error: str | None = None
