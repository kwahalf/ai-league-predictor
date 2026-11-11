"""A provider that needs no credentials, no network and no luck.

It exists so the whole feature — loop, tool dispatch, approval, transcript, UI —
is runnable and testable before anyone has an API key, and so the test suite is
hermetic. It is domain-agnostic on purpose: it calls each offered tool once, in
the order offered, then answers. Whatever tools a repo user writes, this exercises
them.

Arguments are synthesised from each tool's own schema, so they satisfy the same
validation a real model's arguments face. The values are placeholders, so tools
will usually return empty results — that is the point being demonstrated (the
loop works), not a pretence that the answer is meaningful.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .types import AssistantTurn, LlmRequest, ToolCall, Usage

PLACEHOLDERS: dict[str, Any] = {
    "string": "example",
    "integer": 1,
    "number": 1,
    "boolean": True,
    "array": [],
    "object": {},
}


def call(request: LlmRequest) -> AssistantTurn:
    called = _tools_already_called(request.messages)
    remaining = [t for t in request.tools if t["name"] not in called]

    if not request.tools_enabled or not remaining:
        return _answer(request, called)
    return _call_tool(request, remaining[0], called)


# ── Turn builders ─────────────────────────────────────────────────────────


def _call_tool(request: LlmRequest, tool: dict[str, Any], called: set[str]) -> AssistantTurn:
    call_id = _tool_call_id(tool["name"], len(called))
    arguments = _synthesise_arguments(tool.get("input_schema", {}))
    raw = [
        {"type": "text", "text": f'Checking {tool["name"]}.'},
        {"type": "tool_use", "id": call_id, "name": tool["name"], "input": arguments},
    ]
    return AssistantTurn(
        raw=raw,
        model=request.model,
        tool_calls=[ToolCall(id=call_id, name=tool["name"], arguments=arguments)],
        text=f'Checking {tool["name"]}.',
        final=False,
        usage=_usage(request, raw),
    )


def _answer(request: LlmRequest, called: set[str]) -> AssistantTurn:
    tools_used = ", ".join(sorted(called)) or "no tools"
    summary = f"Fake provider answered using {tools_used}."
    answer = _synthesise_answer(request.answer_schema, summary, sorted(called))
    text = json.dumps(answer) if answer is not None else summary
    raw = [{"type": "text", "text": text}]
    return AssistantTurn(
        raw=raw,
        model=request.model,
        tool_calls=[],
        text=text,
        answer=answer,
        final=True,
        usage=_usage(request, raw),
    )


# ── Transcript reading ────────────────────────────────────────────────────


def _tools_already_called(messages: list[dict[str, Any]]) -> set[str]:
    """Which tools have a recorded call, read from the transcript itself.

    Read from history rather than kept in memory because the loop is durable:
    the same request may be replayed on a different worker with no local state.
    """
    called: set[str] = set()
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                called.add(str(block.get("name")))
    return called


# ── Synthesis ─────────────────────────────────────────────────────────────


def _synthesise_arguments(schema: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = schema.get("properties", {})
    required: list[str] = schema.get("required", [])
    arguments: dict[str, Any] = {}
    for name in required:
        spec = properties.get(name, {})
        arguments[name] = _placeholder(spec)
    return arguments


def _synthesise_answer(
    schema: dict[str, Any] | None, summary: str, tools_used: list[str]
) -> dict[str, Any] | None:
    if not schema:
        return None
    answer: dict[str, Any] = {}
    for name, spec in schema.get("properties", {}).items():
        if name not in schema.get("required", list(schema.get("properties", {}))):
            continue
        if spec.get("type") == "string":
            answer[name] = summary
        elif spec.get("type") == "array":
            answer[name] = [f"Called {t}." for t in tools_used] or ["No tools were called."]
        else:
            answer[name] = _placeholder(spec)
    return answer


def _placeholder(spec: dict[str, Any]) -> Any:
    if "enum" in spec and spec["enum"]:
        return spec["enum"][0]
    if "default" in spec:
        return spec["default"]
    return PLACEHOLDERS.get(spec.get("type", "string"), "example")


def _tool_call_id(tool_name: str, round_index: int) -> str:
    # Deterministic, so a replayed workflow produces identical history, and
    # distinct per round, so approvals bind to one call.
    digest = hashlib.sha256(f"{tool_name}/{round_index}".encode()).hexdigest()[:12]
    return f"fake_{digest}"


def _usage(request: LlmRequest, raw: list[dict[str, Any]]) -> Usage:
    # Character counts, not tokens. Enough for the transcript to show movement
    # without pretending to be a tokenizer.
    return Usage(
        input_tokens=len(json.dumps(request.messages)) // 4,
        output_tokens=len(json.dumps(raw)) // 4,
    )
