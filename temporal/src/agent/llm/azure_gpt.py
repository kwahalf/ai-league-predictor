"""Azure OpenAI (GPT) adapter — an address for the OpenAI chat-completions format.

Claude on Azure goes through `claude.py` with the Foundry client; this module is
for GPT deployments. The wire format itself lives in `openai_chat.py`, shared with
any other endpoint speaking it, so all this module owns is the URL, the
authentication header and its own error type.

Azure serves chat completions at two different paths, and a given resource
generally accepts one of them rather than both:

    deployments  {base}/openai/deployments/{deployment}/chat/completions
                 The long-standing shape. Works on every Azure OpenAI resource,
                 including `*.cognitiveservices.azure.com` ones, and takes a
                 dated api-version. The default, because it is the one that
                 always works.
    v1           {base}/openai/v1/chat/completions
                 The newer OpenAI-compatible surface, which rejects most dated
                 api-versions with "API version not supported".

`AZURE_OPENAI_API_STYLE` picks. The deployment name defaults to the model named in
the agent's YAML, which is right whenever a deployment is named after the model it
serves; `AZURE_OPENAI_DEPLOYMENT` overrides it when it is not.
"""

from __future__ import annotations

import os

from . import openai_chat
from .openai_chat import build_body, normalise, to_openai_messages  # noqa: F401 — public surface
from .types import AssistantTurn, LlmRequest

DEFAULT_API_VERSION = "2025-03-01-preview"
REQUEST_TIMEOUT_SECONDS = openai_chat.REQUEST_TIMEOUT_SECONDS

API_STYLES = ("deployments", "v1")
DEFAULT_API_STYLE = "deployments"


class AzureConfigError(Exception):
    """Azure endpoint or credentials are missing."""


class AzureRequestError(Exception):
    """Azure returned an error. Retried by Temporal when transient."""


# ── Configuration ─────────────────────────────────────────────────────────


def endpoint_url(model: str) -> str:
    base = os.environ.get("AZURE_OPENAI_BASE_URL") or os.environ.get("AZURE_API_BASE")
    if not base:
        raise AzureConfigError(
            "AZURE_OPENAI_BASE_URL (or AZURE_API_BASE) is required for provider azure_gpt, "
            "for example https://my-resource.openai.azure.com/"
        )

    style = (os.environ.get("AZURE_OPENAI_API_STYLE") or DEFAULT_API_STYLE).strip().lower()
    if style not in API_STYLES:
        raise AzureConfigError(
            f'AZURE_OPENAI_API_STYLE must be one of: {", ".join(API_STYLES)} (got "{style}"). '
            "Use `deployments` unless your resource serves the newer /openai/v1 surface."
        )

    version = os.environ.get("AZURE_OPENAI_API_VERSION", DEFAULT_API_VERSION)
    root = base.rstrip("/")

    if style == "v1":
        return f"{root}/openai/v1/chat/completions?api-version={version}"

    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT") or model
    return f"{root}/openai/deployments/{deployment}/chat/completions?api-version={version}"


def api_key() -> str:
    key = os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("AZURE_API_KEY")
    if not key:
        raise AzureConfigError(
            "AZURE_OPENAI_API_KEY (or AZURE_API_KEY) is required for provider azure_gpt"
        )
    return key


def call(request: LlmRequest) -> AssistantTurn:
    return openai_chat.post(
        request,
        url=endpoint_url(request.model),
        headers={"api-key": api_key()},
        error_cls=AzureRequestError,
        label="Azure OpenAI",
    )
