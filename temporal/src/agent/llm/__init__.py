"""Provider selection — the only place that knows which vendor is in play.

Nothing above this package sees a provider SDK: the workflow and the tool
registry deal in `LlmRequest` and `AssistantTurn` only. Adding a provider means
adding a module here and a name to `_PROVIDERS`.

`claude` covers Claude wherever it is hosted (first-party API, Bedrock,
Microsoft Foundry, Vertex) because those share one request surface and differ
only by client construction.

`azure_gpt` and `litellm` are two addresses for one wire format — the OpenAI
chat-completions shape, translated once in `openai_chat.py`. Each of those modules
owns only its URL, its auth header and its error type. A third endpoint speaking
the same format should be another short module, not another translation.
"""

from __future__ import annotations

from .types import AssistantTurn, LlmRequest

_PROVIDERS = ("claude", "bedrock", "foundry", "vertex", "azure_gpt", "litellm", "fake")


class UnknownProvider(Exception):
    """A configured provider has no adapter."""


def call_model(request: LlmRequest) -> AssistantTurn:
    provider = request.provider

    if provider == "fake":
        from . import fake

        return fake.call(request)

    if provider in ("claude", "bedrock", "foundry", "vertex"):
        from . import claude

        return claude.call(request)

    if provider == "azure_gpt":
        from . import azure_gpt

        return azure_gpt.call(request)

    if provider == "litellm":
        from . import litellm

        return litellm.call(request)

    raise UnknownProvider(
        f'Unknown provider "{provider}". Available: {", ".join(_PROVIDERS)}'
    )


__all__ = ["AssistantTurn", "LlmRequest", "UnknownProvider", "call_model"]
