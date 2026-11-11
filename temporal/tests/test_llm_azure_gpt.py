"""The Azure OpenAI (GPT) adapter — the translation the seam exists for.

The canonical transcript is the Anthropic message shape, so this adapter has to
translate in both directions. What these tests protect is that the loop above the
seam never sees the difference: a turn from Azure produces the same canonical
`raw` blocks a Claude turn would, and gets translated back on the next round.
"""

import json

import pytest

from src.agent.llm import azure_gpt
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
        provider="azure_gpt",
        model="gpt-4o",
        system_prompt="You are a test agent.",
        messages=[{"role": "user", "content": "How many portfolios?"}],
        tools=TOOLS,
        answer_schema=ANSWER_SCHEMA,
        max_tokens=1024,
        tools_enabled=True,
    )
    defaults.update(overrides)
    return LlmRequest(**defaults)


# ── Outbound translation ──────────────────────────────────────────────────


def test_tools_become_openai_function_declarations() -> None:
    body = azure_gpt.build_body(make_request())

    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "search_entities",
                "description": "Search.",
                "parameters": TOOLS[0]["input_schema"],
            },
        }
    ]


def test_the_system_prompt_becomes_the_first_message() -> None:
    body = azure_gpt.build_body(make_request())

    assert body["messages"][0] == {"role": "system", "content": "You are a test agent."}
    assert body["messages"][1] == {"role": "user", "content": "How many portfolios?"}


def test_an_assistant_tool_call_is_translated_to_tool_calls() -> None:
    canonical = [
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Looking."},
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "search_entities",
                    "input": {"entity_type": "portfolio"},
                },
            ],
        },
    ]

    body = azure_gpt.build_body(make_request(messages=canonical))

    assistant = body["messages"][2]
    assert assistant["role"] == "assistant"
    assert assistant["content"] == "Looking."
    assert assistant["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "search_entities",
                "arguments": json.dumps({"entity_type": "portfolio"}),
            },
        }
    ]


def test_tool_results_become_one_tool_message_each() -> None:
    canonical = [
        {"role": "user", "content": "q"},
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": '{"n": 3}'},
                {"type": "tool_result", "tool_use_id": "call_2", "content": "failed"},
            ],
        },
    ]

    body = azure_gpt.build_body(make_request(messages=canonical))

    tool_messages = [m for m in body["messages"] if m["role"] == "tool"]
    assert tool_messages == [
        {"role": "tool", "tool_call_id": "call_1", "content": '{"n": 3}'},
        {"role": "tool", "tool_call_id": "call_2", "content": "failed"},
    ]


def test_the_answer_schema_becomes_a_strict_response_format() -> None:
    body = azure_gpt.build_body(make_request())

    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == ANSWER_SCHEMA
    assert body["response_format"]["json_schema"]["strict"] is True


def test_tools_are_withheld_on_the_final_round() -> None:
    body = azure_gpt.build_body(make_request(tools_enabled=False))

    assert "tools" not in body


# ── Inbound translation ───────────────────────────────────────────────────


def test_a_tool_call_reply_produces_canonical_blocks() -> None:
    reply = {
        "model": "gpt-4o",
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": "Looking.",
                    "tool_calls": [
                        {
                            "id": "call_9",
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
    }

    turn = azure_gpt.normalise(reply, make_request())

    assert turn.final is False
    assert [(c.id, c.name, c.arguments) for c in turn.tool_calls] == [
        ("call_9", "search_entities", {"entity_type": "portfolio"})
    ]
    # Canonical shape, so the loop and the next round's translation both work.
    assert turn.raw == [
        {"type": "text", "text": "Looking."},
        {
            "type": "tool_use",
            "id": "call_9",
            "name": "search_entities",
            "input": {"entity_type": "portfolio"},
        },
    ]
    assert turn.usage.input_tokens == 100
    assert turn.usage.output_tokens == 20


def test_a_final_reply_parses_the_structured_answer() -> None:
    reply = {
        "model": "gpt-4o",
        "usage": {},
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": '{"summary": "Three."}'},
            }
        ],
    }

    turn = azure_gpt.normalise(reply, make_request())

    assert turn.final is True
    assert turn.answer == {"summary": "Three."}


def test_unparseable_tool_arguments_are_reported_not_raised() -> None:
    reply = {
        "model": "gpt-4o",
        "usage": {},
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_bad",
                            "type": "function",
                            "function": {"name": "search_entities", "arguments": "{not json"},
                        }
                    ],
                },
            }
        ],
    }

    turn = azure_gpt.normalise(reply, make_request())

    # An empty argument set fails the tool's own validation, which returns an
    # error result the model can read — better than crashing the run.
    assert turn.tool_calls[0].arguments == {}


def test_a_content_filter_is_reported_as_a_refusal() -> None:
    reply = {
        "model": "gpt-4o",
        "usage": {},
        "choices": [{"finish_reason": "content_filter", "message": {"content": None}}],
    }

    turn = azure_gpt.normalise(reply, make_request())

    assert turn.refused is True
    assert turn.final is True


# ── Endpoint construction ─────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_azure_env(monkeypatch):
    """Azure config comes from the environment, so each test starts from none."""
    for name in (
        "AZURE_OPENAI_BASE_URL",
        "AZURE_API_BASE",
        "AZURE_OPENAI_API_VERSION",
        "AZURE_OPENAI_API_STYLE",
        "AZURE_OPENAI_DEPLOYMENT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_the_endpoint_is_built_from_the_azure_environment(monkeypatch) -> None:
    # The deployment-scoped path is the default, because it is the one every Azure
    # resource accepts. The deployment comes from the model the agent config names.
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2025-03-01-preview")

    url = azure_gpt.endpoint_url("gpt-4o")

    assert url == (
        "https://example.openai.azure.com/openai/deployments/gpt-4o/chat/completions"
        "?api-version=2025-03-01-preview"
    )


def test_a_deployment_named_differently_from_the_model_is_honoured(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "my-prod-deployment")

    assert azure_gpt.endpoint_url("gpt-5.5") == (
        "https://example.openai.azure.com/openai/deployments/my-prod-deployment"
        "/chat/completions?api-version=2025-04-01-preview"
    )


def test_the_newer_v1_surface_can_be_selected(monkeypatch) -> None:
    # A resource serving /openai/v1 ignores the deployment entirely: the model
    # travels in the body instead of the path.
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "preview")
    monkeypatch.setenv("AZURE_OPENAI_API_STYLE", "v1")

    assert azure_gpt.endpoint_url("gpt-4o") == (
        "https://example.openai.azure.com/openai/v1/chat/completions?api-version=preview"
    )


def test_an_unknown_api_style_is_refused_by_name(monkeypatch) -> None:
    # Rather than silently falling back and producing a 404 nobody can explain.
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_STYLE", "responses")

    with pytest.raises(azure_gpt.AzureConfigError) as exc:
        azure_gpt.endpoint_url("gpt-4o")

    assert "responses" in str(exc.value)
    assert "deployments" in str(exc.value)


def test_a_missing_endpoint_is_refused_with_the_variable_name(monkeypatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("AZURE_API_BASE", raising=False)

    try:
        azure_gpt.endpoint_url("gpt-4o")
    except azure_gpt.AzureConfigError as exc:
        assert "AZURE_OPENAI_BASE_URL" in str(exc)
    else:
        raise AssertionError("expected AzureConfigError")
