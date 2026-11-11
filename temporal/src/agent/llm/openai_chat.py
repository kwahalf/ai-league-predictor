"""The OpenAI chat-completions wire format, shared by every endpoint that speaks it.

Azure OpenAI and a LiteLLM proxy are not two protocols, they are two addresses for
one protocol. The translation lives here so there is a single copy of it:

    tools           {"type": "function", "function": {...}}   not input_schema
    tool calls      message.tool_calls[] with a JSON *string*  not tool_use blocks
    tool results    {"role": "tool", ...}, one per result      not one user turn
    output schema   response_format.json_schema                not output_config

Everything above the seam keeps the canonical Anthropic message shape (see
`types.py`), so this module translates outbound in `build_body` and inbound in
`normalise`. The `raw` it returns is canonical, which is what lets the loop mix
providers across rounds without noticing.

What each provider module still owns: its URL, its authentication header, and its
own error type. Nothing else, and if a provider needs more than that it is not
really speaking this format.

Called over plain HTTP rather than through the `openai` package: one request shape
is cheaper to read than another dependency, and the worker already speaks urllib.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .types import AssistantTurn, LlmRequest, ToolCall, Usage

REQUEST_TIMEOUT_SECONDS = 120


# ── Outbound: canonical → OpenAI ──────────────────────────────────────────


def build_body(request: LlmRequest) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": request.model,
        "max_completion_tokens": request.max_tokens,
        "messages": to_openai_messages(request),
    }
    if request.tools_enabled and request.tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                },
            }
            for tool in request.tools
        ]
    if request.answer_schema:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "answer",
                "schema": request.answer_schema,
                "strict": True,
            },
        }
    return body


def to_openai_messages(request: LlmRequest) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": request.system_prompt}
    ]

    for message in request.messages:
        role = message.get("role")
        content = message.get("content")

        if isinstance(content, str):
            messages.append({"role": role, "content": content})
            continue

        blocks = content or []
        if role == "assistant":
            messages.append(_assistant_message(blocks))
            continue

        # A canonical user turn carrying tool results becomes one tool message
        # per result; anything else in it stays user text.
        results = [b for b in blocks if b.get("type") == "tool_result"]
        for block in results:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": block.get("tool_use_id"),
                    "content": _as_text(block.get("content")),
                }
            )
        text = " ".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        if text:
            messages.append({"role": "user", "content": text})

    return messages


def _assistant_message(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    tool_calls = [
        {
            "id": block.get("id"),
            "type": "function",
            "function": {
                "name": block.get("name"),
                "arguments": json.dumps(block.get("input") or {}),
            },
        }
        for block in blocks
        if block.get("type") == "tool_use"
    ]
    message: dict[str, Any] = {"role": "assistant", "content": text or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


# ── Inbound: OpenAI → canonical ───────────────────────────────────────────


def normalise(reply: dict[str, Any], request: LlmRequest) -> AssistantTurn:
    choice = (reply.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    finish_reason = choice.get("finish_reason")
    usage = reply.get("usage") or {}
    model = str(reply.get("model") or request.model)

    text = message.get("content")
    raw: list[dict[str, Any]] = []
    if text:
        raw.append({"type": "text", "text": text})

    tool_calls: list[ToolCall] = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        arguments = _parse_arguments(function.get("arguments"))
        tool_calls.append(
            ToolCall(id=str(call.get("id")), name=str(function.get("name")), arguments=arguments)
        )
        raw.append(
            {
                "type": "tool_use",
                "id": str(call.get("id")),
                "name": str(function.get("name")),
                "input": arguments,
            }
        )

    if finish_reason == "content_filter":
        return AssistantTurn(
            raw=raw,
            model=model,
            text=text,
            final=True,
            refused=True,
            usage=_usage(usage),
        )

    final = not tool_calls
    return AssistantTurn(
        raw=raw,
        model=model,
        tool_calls=tool_calls,
        text=text,
        answer=_parse_answer(text) if final and request.answer_schema else None,
        final=final,
        usage=_usage(usage),
    )


# ── The request ───────────────────────────────────────────────────────────


def post(
    request: LlmRequest,
    *,
    url: str,
    headers: dict[str, str],
    error_cls: type[Exception],
    label: str,
) -> AssistantTurn:
    """One chat-completions call.

    `error_cls` and `label` belong to the caller so each provider keeps raising its
    own exception type with its own name in the message — a Temporal retry policy
    and a reader both benefit from knowing which endpoint failed.
    """
    payload = json.dumps(build_body(request)).encode()
    http_request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(http_request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            reply = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # The body can carry credentials-adjacent detail, so only the status and
        # a short excerpt are surfaced.
        detail = exc.read().decode(errors="replace")[:200] if exc.fp else ""
        raise error_cls(f"{label} returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise error_cls(f"{label} is unreachable: {exc.reason}") from exc

    return normalise(reply, request)


# ── Helpers ───────────────────────────────────────────────────────────────


def _parse_arguments(arguments: Any) -> dict[str, Any]:
    """Tool arguments arrive as a JSON string, and may not be valid JSON.

    An unparseable call becomes an empty argument set, which the tool's own
    schema then rejects — so the model gets a readable error result instead of
    the run dying on a malformed function call.
    """
    if isinstance(arguments, dict):
        return arguments
    if not arguments:
        return {}
    try:
        parsed = json.loads(arguments)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_answer(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _as_text(content: Any) -> str:
    return content if isinstance(content, str) else json.dumps(content, default=str)


def _usage(usage: dict[str, Any]) -> Usage:
    return Usage(
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
    )
