"""The LiteLLM proxy adapter.

The wire format is not tested here — it is `openai_chat.py`, tested once via
`test_llm_azure_gpt.py`. What is this adapter's own is the URL, the bearer token
and the error type, so that is what these tests cover. The last test in the file
pins the sharing itself, so a future edit cannot quietly fork the translation.
"""

import json
from urllib.error import HTTPError, URLError

import pytest

from src.agent.llm import azure_gpt, litellm, openai_chat
from src.agent.llm.types import LlmRequest

ENV_VARS = ("LITELLM_BASE_URL", "LITELLM_API_BASE", "LITELLM_API_KEY", "LITELLM_MASTER_KEY")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def make_request(**overrides) -> LlmRequest:
    defaults = dict(
        provider="litellm",
        model="gpt-5.5",
        system_prompt="You are a test agent.",
        messages=[{"role": "user", "content": "How many portfolios?"}],
        tools=[],
        answer_schema=None,
        max_tokens=1024,
        tools_enabled=True,
    )
    defaults.update(overrides)
    return LlmRequest(**defaults)


# ── The URL ───────────────────────────────────────────────────────────────


def test_the_chat_completions_path_is_appended_to_a_bare_host(monkeypatch) -> None:
    monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm:4000")

    assert litellm.endpoint_url() == "http://litellm:4000/v1/chat/completions"


def test_a_trailing_slash_does_not_double_up(monkeypatch) -> None:
    monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm:4000/")

    assert litellm.endpoint_url() == "http://litellm:4000/v1/chat/completions"


def test_a_base_that_already_ends_in_v1_is_not_given_a_second_one(monkeypatch) -> None:
    # Appending blindly here yields /v1/v1/chat/completions, and a 404 from a
    # doubled path is a miserable thing to debug in front of a room.
    monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm:4000/v1")

    assert litellm.endpoint_url() == "http://litellm:4000/v1/chat/completions"


def test_a_full_endpoint_is_used_as_given(monkeypatch) -> None:
    monkeypatch.setenv("LITELLM_BASE_URL", "https://proxy.internal/v1/chat/completions")

    assert litellm.endpoint_url() == "https://proxy.internal/v1/chat/completions"


def test_the_alternative_variable_name_is_accepted(monkeypatch) -> None:
    monkeypatch.setenv("LITELLM_API_BASE", "http://localhost:4000")

    assert litellm.endpoint_url() == "http://localhost:4000/v1/chat/completions"


def test_a_missing_address_is_refused_with_the_variable_name() -> None:
    with pytest.raises(litellm.LiteLlmConfigError) as exc:
        litellm.endpoint_url()

    assert "LITELLM_BASE_URL" in str(exc.value)


# ── Authentication ────────────────────────────────────────────────────────


def test_a_key_becomes_a_bearer_token(monkeypatch) -> None:
    monkeypatch.setenv("LITELLM_API_KEY", "sk-team-3")

    assert litellm.auth_headers() == {"Authorization": "Bearer sk-team-3"}


def test_the_master_key_is_never_used_as_the_workers_token(monkeypatch) -> None:
    """The proxy's admin credential is not this worker's bearer token.

    Accepting it would work, and would make the worker an administrator of the
    thing meant to be constraining it — able to mint keys and read every team's
    spend. The worker gets a scoped key or it goes unauthenticated.
    """
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-master")

    assert litellm.auth_headers() == {}


def test_no_key_means_no_header() -> None:
    # A proxy started without a master key accepts unauthenticated calls, which is
    # a legitimate way to run one locally. One that wants a key answers 401 with a
    # readable message, which beats this module guessing.
    assert litellm.auth_headers() == {}


# ── The call ──────────────────────────────────────────────────────────────


def test_the_request_carries_the_model_alias_and_the_bearer_token(monkeypatch) -> None:
    monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm:4000")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-team-3")
    seen: dict = {}

    def fake_post(request, *, url, headers, error_cls, label):
        seen.update(url=url, headers=headers, error_cls=error_cls, label=label, body=openai_chat.build_body(request))
        return "sentinel"

    monkeypatch.setattr(litellm.openai_chat, "post", fake_post)

    assert litellm.call(make_request()) == "sentinel"
    assert seen["url"] == "http://litellm:4000/v1/chat/completions"
    assert seen["headers"] == {"Authorization": "Bearer sk-team-3"}
    assert seen["error_cls"] is litellm.LiteLlmRequestError
    # The proxy's alias, straight from the agent's YAML. Nothing here knows which
    # vendor is behind it.
    assert seen["body"]["model"] == "gpt-5.5"


def test_an_http_error_becomes_a_litellm_request_error(monkeypatch) -> None:
    monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm:4000")

    def boom(*args, **kwargs):
        raise HTTPError("http://litellm:4000", 429, "Too Many Requests", {}, None)

    monkeypatch.setattr(openai_chat.urllib.request, "urlopen", boom)

    with pytest.raises(litellm.LiteLlmRequestError) as exc:
        litellm.call(make_request())

    assert "429" in str(exc.value)
    # Names the proxy, so a reader knows which hop failed.
    assert "LiteLLM proxy" in str(exc.value)


def test_an_unreachable_proxy_says_so(monkeypatch) -> None:
    monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm:4000")

    def boom(*args, **kwargs):
        raise URLError("connection refused")

    monkeypatch.setattr(openai_chat.urllib.request, "urlopen", boom)

    with pytest.raises(litellm.LiteLlmRequestError) as exc:
        litellm.call(make_request())

    assert "unreachable" in str(exc.value)


def test_a_reply_is_normalised_by_the_shared_translation(monkeypatch) -> None:
    monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm:4000")
    reply = {
        "model": "gpt-5.5",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "search_entities",
                                "arguments": '{"entity_type": "portfolio"}',
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 5},
    }

    class Response:
        def read(self):
            return json.dumps(reply).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(openai_chat.urllib.request, "urlopen", lambda *a, **k: Response())

    turn = litellm.call(make_request())

    # Canonical Anthropic-shaped blocks, exactly as a Claude turn would produce.
    assert turn.raw == [
        {
            "type": "tool_use",
            "id": "call_1",
            "name": "search_entities",
            "input": {"entity_type": "portfolio"},
        }
    ]
    assert turn.tool_calls[0].name == "search_entities"
    assert turn.final is False
    assert turn.usage.input_tokens == 11


# ── The seam ──────────────────────────────────────────────────────────────


def test_both_openai_format_providers_share_one_translation() -> None:
    """Two addresses, one wire format.

    If someone later copies the translation into a provider module, this fails —
    which is the point. Divergence here is how the two adapters would quietly stop
    agreeing about what a tool call looks like.
    """
    assert azure_gpt.build_body is openai_chat.build_body
    assert azure_gpt.normalise is openai_chat.normalise


def test_litellm_is_an_offered_provider() -> None:
    from src.agent import llm

    assert "litellm" in llm._PROVIDERS
