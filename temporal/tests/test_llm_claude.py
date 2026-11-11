"""The Claude adapter: request translation and response normalisation.

Both halves are pure functions over plain dicts, so these tests need no network,
no credentials and no SDK objects. `call()` is exercised with a stub client to
prove the wiring, not the vendor.
"""

import pytest

from src.agent.llm import claude
from src.agent.llm.types import LlmRequest

TOOLS = [
    {
        "name": "search_entities",
        "description": "Search.",
        "input_schema": {
            "type": "object",
            "properties": {"entity_type": {"type": "string"}},
            "required": ["entity_type"],
            "additionalProperties": False,
        },
    }
]

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}


def make_request(**overrides) -> LlmRequest:
    defaults = dict(
        provider="claude",
        model="claude-opus-5",
        system_prompt="You are a test agent.",
        messages=[{"role": "user", "content": "How many portfolios?"}],
        tools=TOOLS,
        answer_schema=ANSWER_SCHEMA,
        max_tokens=2048,
        tools_enabled=True,
    )
    defaults.update(overrides)
    return LlmRequest(**defaults)


# ── Request translation ───────────────────────────────────────────────────


def test_params_carry_model_system_messages_and_tools() -> None:
    params = claude.build_params(make_request())

    assert params["model"] == "claude-opus-5"
    assert params["system"] == "You are a test agent."
    assert params["messages"] == [{"role": "user", "content": "How many portfolios?"}]
    assert params["max_tokens"] == 2048
    assert [t["name"] for t in params["tools"]] == ["search_entities"]


def test_tools_are_withheld_on_the_final_round() -> None:
    params = claude.build_params(make_request(tools_enabled=False))

    assert "tools" not in params


def test_answer_schema_becomes_a_strict_output_format() -> None:
    params = claude.build_params(make_request())

    assert params["output_config"] == {
        "format": {"type": "json_schema", "schema": ANSWER_SCHEMA}
    }


def test_no_output_format_without_an_answer_schema() -> None:
    params = claude.build_params(make_request(answer_schema=None))

    assert "output_config" not in params


def test_thinking_is_left_to_the_model_default() -> None:
    # Opus 5 runs adaptive thinking when `thinking` is omitted, and an explicit
    # value is rejected by some models — so the adapter must not send one.
    params = claude.build_params(make_request())

    assert "thinking" not in params


# ── Response normalisation ────────────────────────────────────────────────


def test_tool_use_blocks_become_tool_calls() -> None:
    message = {
        "model": "claude-opus-5",
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 120, "output_tokens": 30},
        "content": [
            {"type": "text", "text": "Looking that up."},
            {
                "type": "tool_use",
                "id": "toolu_01",
                "name": "search_entities",
                "input": {"entity_type": "portfolio"},
            },
        ],
    }

    turn = claude.normalise(message, make_request())

    assert turn.final is False
    assert [(c.id, c.name, c.arguments) for c in turn.tool_calls] == [
        ("toolu_01", "search_entities", {"entity_type": "portfolio"})
    ]
    assert turn.usage.input_tokens == 120
    assert turn.usage.output_tokens == 30


def test_raw_keeps_every_block_including_thinking() -> None:
    message = {
        "model": "claude-opus-5",
        "stop_reason": "tool_use",
        "usage": {},
        "content": [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "tool_use", "id": "toolu_01", "name": "search_entities", "input": {}},
        ],
    }

    turn = claude.normalise(message, make_request())

    # Thinking blocks must be echoed back unchanged on the next request.
    assert turn.raw == message["content"]


def test_structured_answer_is_parsed_when_the_model_finishes() -> None:
    message = {
        "model": "claude-opus-5",
        "stop_reason": "end_turn",
        "usage": {},
        "content": [{"type": "text", "text": '{"summary": "Three portfolios."}'}],
    }

    turn = claude.normalise(message, make_request())

    assert turn.final is True
    assert turn.answer == {"summary": "Three portfolios."}


def test_prose_instead_of_json_is_kept_rather_than_raising() -> None:
    message = {
        "model": "claude-opus-5",
        "stop_reason": "end_turn",
        "usage": {},
        "content": [{"type": "text", "text": "There are three portfolios."}],
    }

    turn = claude.normalise(message, make_request())

    assert turn.final is True
    assert turn.answer is None
    assert turn.text == "There are three portfolios."


def test_a_refusal_is_reported_not_raised() -> None:
    message = {
        "model": "claude-opus-5",
        "stop_reason": "refusal",
        "stop_details": {"type": "refusal", "category": "cyber"},
        "usage": {},
        "content": [],
    }

    turn = claude.normalise(message, make_request())

    assert turn.refused is True
    assert turn.final is True


# ── Client selection ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "provider,expected",
    [
        ("claude", "Anthropic"),
        ("bedrock", "AnthropicBedrockMantle"),
        ("foundry", "AnthropicFoundry"),
        ("vertex", "AnthropicVertex"),
    ],
)
def test_each_hosting_option_uses_its_own_client_class(provider: str, expected: str) -> None:
    assert claude.client_class_for(provider).__name__ == expected


def test_an_unsupported_hosting_option_is_refused_by_name() -> None:
    with pytest.raises(claude.ClaudeConfigError, match="carrier_pigeon"):
        claude.client_class_for("carrier_pigeon")


# ── Wiring ────────────────────────────────────────────────────────────────


def test_call_sends_built_params_and_normalises_the_reply() -> None:
    sent = {}

    class StubMessages:
        def create(self, **params):
            sent.update(params)

            class Reply:
                def model_dump(self, mode="json"):
                    return {
                        "model": "claude-opus-5",
                        "stop_reason": "end_turn",
                        "usage": {"input_tokens": 5, "output_tokens": 7},
                        "content": [{"type": "text", "text": '{"summary": "done"}'}],
                    }

            return Reply()

    class StubClient:
        messages = StubMessages()

    turn = claude.call(make_request(), client=StubClient())

    assert sent["model"] == "claude-opus-5"
    assert turn.answer == {"summary": "done"}
    assert turn.usage.output_tokens == 7
