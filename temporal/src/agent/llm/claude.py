"""Claude adapter — one code path for Claude wherever it is hosted.

The first-party API, Bedrock, Microsoft Foundry and Vertex all expose the same
`messages.create` surface, so hosting is a client-construction choice rather than
an adapter. Only the credentials differ, and each client reads its own from the
environment:

    claude   ANTHROPIC_API_KEY (or an `ant auth login` profile)
    bedrock  AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY + AWS_REGION
    foundry  AZURE_OPENAI_API_KEY-style Foundry credentials + resource
    vertex   GCP application default credentials + project/region

Two deliberate omissions:

* NO `thinking` PARAMETER. Claude Opus 5 runs adaptive thinking when the
  parameter is omitted, while several models reject an explicit value, so not
  sending one is both the best default and the most portable.
* NO REFUSAL FALLBACK. Server-side fallbacks live on the beta namespace of the
  pinned SDK; rather than put a template on a beta surface, a refusal is
  reported as `AssistantTurn.refused` and the run fails with a clear message.
  Enabling fallbacks is a documented extension, not a hidden default.
"""

from __future__ import annotations

import json
from typing import Any

from .types import AssistantTurn, LlmRequest, ToolCall, Usage


class ClaudeConfigError(Exception):
    """A provider name has no Claude client, or credentials are missing."""


_CLIENT_CLASS_NAMES: dict[str, str] = {
    "claude": "Anthropic",
    "bedrock": "AnthropicBedrockMantle",
    "foundry": "AnthropicFoundry",
    "vertex": "AnthropicVertex",
}


def client_class_for(provider: str) -> type:
    """The SDK client class for a hosting option, without constructing it."""
    import anthropic

    try:
        return getattr(anthropic, _CLIENT_CLASS_NAMES[provider])
    except KeyError:
        raise ClaudeConfigError(
            f'Provider "{provider}" is not a Claude hosting option. '
            f'Expected one of: {", ".join(_CLIENT_CLASS_NAMES)}'
        ) from None


def build_client(provider: str) -> Any:
    """Construct the client, letting each SDK class resolve its own credentials."""
    klass = client_class_for(provider)
    try:
        return klass()
    except Exception as exc:  # noqa: BLE001 — surfaced as a config problem, with the cause
        raise ClaudeConfigError(
            f'Could not construct the {klass.__name__} client for provider "{provider}": {exc}. '
            "Check the credentials that provider reads from the environment."
        ) from exc


def build_params(request: LlmRequest) -> dict[str, Any]:
    params: dict[str, Any] = {
        "model": request.model,
        "max_tokens": request.max_tokens,
        "system": request.system_prompt,
        "messages": request.messages,
    }
    # Withheld on the final round so the model must answer instead of calling more tools.
    if request.tools_enabled and request.tools:
        params["tools"] = request.tools
    if request.answer_schema:
        params["output_config"] = {
            "format": {"type": "json_schema", "schema": request.answer_schema}
        }
    return params


def normalise(message: dict[str, Any], request: LlmRequest) -> AssistantTurn:
    content: list[dict[str, Any]] = message.get("content") or []
    stop_reason = message.get("stop_reason")
    usage = message.get("usage") or {}

    tool_calls = [
        ToolCall(
            id=str(block.get("id")),
            name=str(block.get("name")),
            arguments=block.get("input") or {},
        )
        for block in content
        if block.get("type") == "tool_use"
    ]

    text = "".join(b.get("text") or "" for b in content if b.get("type") == "text") or None

    if stop_reason == "refusal":
        return AssistantTurn(
            raw=content,
            model=str(message.get("model") or request.model),
            text=text,
            final=True,
            refused=True,
            usage=_usage(usage),
        )

    final = not tool_calls
    answer = _parse_answer(text) if final and request.answer_schema else None

    return AssistantTurn(
        raw=content,
        model=str(message.get("model") or request.model),
        tool_calls=tool_calls,
        text=text,
        answer=answer,
        final=final,
        usage=_usage(usage),
    )


def call(request: LlmRequest, *, client: Any | None = None) -> AssistantTurn:
    active = client if client is not None else build_client(request.provider)
    reply = active.messages.create(**build_params(request))
    return normalise(reply.model_dump(mode="json"), request)


def _usage(usage: dict[str, Any]) -> Usage:
    return Usage(
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
    )


def _parse_answer(text: str | None) -> dict[str, Any] | None:
    """Parse the structured answer, tolerating prose.

    A strict output format makes the final text valid JSON, but a model can still
    finish with prose — for instance when the answer schema was dropped or the
    turn ended early. Losing the words in that case is worse than losing the
    shape, so the caller keeps `text` and decides.
    """
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
