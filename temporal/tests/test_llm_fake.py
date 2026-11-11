"""The fake provider: a real agent loop with no credentials and no network.

It is deliberately domain-agnostic — it calls each offered tool once, in order,
then answers. That makes it useful for any set of tools a repo user writes, and
it makes every test below deterministic.
"""

from src.agent.llm import call_model
from src.agent.llm.types import LlmRequest

TOOLS = [
    {
        "name": "search_entities",
        "description": "Search.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_type": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["entity_type"],
            "additionalProperties": False,
        },
    },
    {
        "name": "count_entities",
        "description": "Count.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "findings"],
    "additionalProperties": False,
}


def request(messages: list[dict], *, tools_enabled: bool = True) -> LlmRequest:
    return LlmRequest(
        provider="fake",
        model="fake-model",
        system_prompt="You are a test agent.",
        messages=messages,
        tools=TOOLS,
        answer_schema=ANSWER_SCHEMA,
        max_tokens=1024,
        tools_enabled=tools_enabled,
    )


def user(text: str) -> dict:
    return {"role": "user", "content": text}


def test_first_turn_calls_the_first_offered_tool() -> None:
    turn = call_model(request([user("How many portfolios?")]))

    assert turn.final is False
    assert [c.name for c in turn.tool_calls] == ["search_entities"]


def test_synthesised_arguments_satisfy_the_tools_schema() -> None:
    turn = call_model(request([user("How many portfolios?")]))

    args = turn.tool_calls[0].arguments
    assert set(args) <= {"entity_type", "limit"}
    assert isinstance(args["entity_type"], str)


def test_each_offered_tool_is_called_once_in_order() -> None:
    messages = [user("How many portfolios?")]
    first = call_model(request(messages))
    messages.append({"role": "assistant", "content": first.raw})
    messages.append(
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": first.tool_calls[0].id,
                    "content": '{"entities": []}',
                }
            ],
        }
    )

    second = call_model(request(messages))

    assert [c.name for c in second.tool_calls] == ["count_entities"]


def test_answers_once_every_tool_has_been_called() -> None:
    messages = [user("How many portfolios?")]
    for _ in TOOLS:
        turn = call_model(request(messages))
        messages.append({"role": "assistant", "content": turn.raw})
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": c.id, "content": "{}"}
                    for c in turn.tool_calls
                ],
            }
        )

    final = call_model(request(messages))

    assert final.final is True
    assert set(final.answer) == {"summary", "findings"}
    assert isinstance(final.answer["findings"], list)


def test_answers_immediately_when_tools_are_disabled() -> None:
    turn = call_model(request([user("Anything")], tools_enabled=False))

    assert turn.final is True
    assert turn.tool_calls == []
    assert set(turn.answer) == {"summary", "findings"}


def test_tool_call_ids_are_unique_per_call() -> None:
    first = call_model(request([user("q")]))
    messages = [
        user("q"),
        {"role": "assistant", "content": first.raw},
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": first.tool_calls[0].id, "content": "{}"}
            ],
        },
    ]
    second = call_model(request(messages))

    assert first.tool_calls[0].id != second.tool_calls[0].id


def test_usage_and_model_are_reported() -> None:
    turn = call_model(request([user("q")]))

    assert turn.model == "fake-model"
    assert turn.usage.input_tokens >= 0
    assert turn.usage.output_tokens >= 0


def test_raw_is_replayable_as_an_assistant_message() -> None:
    turn = call_model(request([user("q")]))

    # The loop appends `raw` verbatim; it must be valid assistant content.
    assert isinstance(turn.raw, list)
    assert any(block.get("type") == "tool_use" for block in turn.raw)
