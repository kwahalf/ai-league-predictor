"""Provider-neutral request and response types.

THE CANONICAL TRANSCRIPT SHAPE IS THE ANTHROPIC MESSAGE SHAPE — a list of
`{role, content}` entries whose content is either a string or a list of blocks
(`text`, `tool_use`, `tool_result`). One wire shape had to be canonical, and
picking a real one beats inventing a third: the Claude adapter passes it through
untouched, and every other adapter translates to and from it.

`AssistantTurn.raw` is the assistant content the provider produced, in that same
canonical shape. The loop appends it verbatim rather than rebuilding it from the
normalised fields, which matters on Claude: thinking blocks must be echoed back
unchanged on the next request, and cherry-picking the text would drop them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    """One tool the model asked for. `id` is the provider's own tool-call id."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class LlmRequest:
    provider: str
    model: str
    system_prompt: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = field(default_factory=list)
    answer_schema: dict[str, Any] | None = None
    max_tokens: int = 4096
    # The final round runs with tools withheld, so an agent that keeps calling
    # tools still produces an answer instead of hitting the round cap silently.
    tools_enabled: bool = True


@dataclass
class AssistantTurn:
    raw: Any
    model: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    text: str | None = None
    answer: dict[str, Any] | None = None
    final: bool = False
    usage: Usage = field(default_factory=Usage)
    # True when the provider declined the request rather than answering it.
    refused: bool = False
