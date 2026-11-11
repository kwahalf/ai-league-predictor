"""LiteLLM proxy adapter — one endpoint in front of many models.

A LiteLLM proxy speaks the OpenAI chat-completions format, so this module reuses
`openai_chat.py` wholesale and owns only three things: the URL, a bearer token, and
its own error type. That is the entire adapter, and it is short because the proxy
is deliberately not a new protocol.

WHY GO THROUGH A PROXY AT ALL. Not to abstract over providers — `call_model`
already does that, above this seam. The proxy earns its place operationally: one
upstream key held in one process, per-team virtual keys with their own spend caps
and rate limits, a request log, and model changes as configuration rather than as
a deploy. For a shared key across a room of people, that is the difference between
a workshop and an incident.

WHAT NOT TO ROUTE THROUGH IT. Claude. The canonical transcript here is the
Anthropic message shape, and sending it Anthropic → OpenAI → LiteLLM → Anthropic
is a lossy round trip: thinking blocks do not survive it. Use `provider: claude`
for Claude models, which talks to them directly, and this adapter for the
OpenAI-family models a proxy is genuinely useful for.

The model named in an agent's YAML is passed through as the proxy's model alias, so
whatever `model_name` appears in the proxy's `config.yaml` is what the agent asks
for. Nothing here needs to know which vendor is behind it.
"""

from __future__ import annotations

import os

from . import openai_chat
from .types import AssistantTurn, LlmRequest

REQUEST_TIMEOUT_SECONDS = openai_chat.REQUEST_TIMEOUT_SECONDS


class LiteLlmConfigError(Exception):
    """The proxy address is missing."""


class LiteLlmRequestError(Exception):
    """The proxy returned an error. Retried by Temporal when transient."""


# ── Configuration ─────────────────────────────────────────────────────────


def endpoint_url() -> str:
    """The proxy's chat-completions URL.

    Forgiving about how much of the path is already in the variable, because
    `http://localhost:4000`, `.../v1` and a full `.../v1/chat/completions` are all
    things people reasonably paste. Appending blindly would produce
    `/v1/v1/chat/completions`, and a 404 from a doubled path is a miserable thing
    to debug in a room full of people.
    """
    base = os.environ.get("LITELLM_BASE_URL") or os.environ.get("LITELLM_API_BASE")
    if not base:
        raise LiteLlmConfigError(
            "LITELLM_BASE_URL (or LITELLM_API_BASE) is required for provider litellm, "
            "for example http://litellm:4000"
        )

    root = base.strip().rstrip("/")
    if root.endswith("/chat/completions"):
        return root
    if root.endswith("/v1"):
        return f"{root}/chat/completions"
    return f"{root}/v1/chat/completions"


def auth_headers() -> dict[str, str]:
    """Bearer token, when there is one.

    Deliberately optional: a proxy started without a master key accepts
    unauthenticated requests, and that is a legitimate way to run one locally for a
    first pass. A proxy that does want a key answers 401 with a readable message,
    which is a better failure than this module guessing.

    `LITELLM_MASTER_KEY` IS NOT READ HERE, ON PURPOSE. The master key is the
    proxy's admin credential — it can mint virtual keys and read every team's
    spend. Accepting it as this worker's token would work, and would quietly make
    the worker an administrator of the thing that is supposed to be constraining
    it. Give the worker a scoped key from `/key/generate` instead.
    """
    key = os.environ.get("LITELLM_API_KEY")
    return {"Authorization": f"Bearer {key}"} if key else {}


def call(request: LlmRequest) -> AssistantTurn:
    return openai_chat.post(
        request,
        url=endpoint_url(),
        headers=auth_headers(),
        error_cls=LiteLlmRequestError,
        label="LiteLLM proxy",
    )
