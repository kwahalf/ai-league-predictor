"""The tool registry: what the model is offered, and what actually runs."""

import pytest

from src.agent.registry import (
    Registry,
    ToolNotFound,
    ToolValidationError,
)


@pytest.fixture
def registry() -> Registry:
    return Registry()


def test_declared_tool_is_offered_to_the_model(registry: Registry) -> None:
    @registry.tool(
        name="echo",
        description="Echo a message back.",
        input_schema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
            "additionalProperties": False,
        },
    )
    def echo(message: str) -> dict:
        return {"message": message}

    assert registry.declarations(["echo"]) == [
        {
            "name": "echo",
            "description": "Echo a message back.",
            "input_schema": {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
                "additionalProperties": False,
            },
        }
    ]


def test_only_the_named_subset_is_offered(registry: Registry) -> None:
    for name in ("read_one", "read_two"):
        registry.tool(
            name=name,
            description=name,
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        )(lambda: {})

    assert [d["name"] for d in registry.declarations(["read_two"])] == ["read_two"]


def test_declaring_an_unknown_tool_names_the_offender(registry: Registry) -> None:
    with pytest.raises(ToolNotFound, match="nonexistent_tool"):
        registry.declarations(["nonexistent_tool"])


def test_dispatch_runs_the_registered_function(registry: Registry) -> None:
    @registry.tool(
        name="add",
        description="Add two numbers.",
        input_schema={
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
            "additionalProperties": False,
        },
    )
    def add(a: int, b: int) -> dict:
        return {"sum": a + b}

    assert registry.dispatch("add", {"a": 2, "b": 3}) == {"sum": 5}


def test_dispatch_rejects_arguments_the_schema_does_not_allow(registry: Registry) -> None:
    @registry.tool(
        name="strict_add",
        description="Add two numbers.",
        input_schema={
            "type": "object",
            "properties": {"a": {"type": "integer"}},
            "required": ["a"],
            "additionalProperties": False,
        },
    )
    def strict_add(a: int) -> dict:
        return {"a": a}

    with pytest.raises(ToolValidationError, match="unexpected"):
        registry.dispatch("strict_add", {"a": 1, "injected": "surprise"})


def test_dispatch_rejects_missing_required_arguments(registry: Registry) -> None:
    @registry.tool(
        name="needs_query",
        description="Search.",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    )
    def needs_query(query: str) -> dict:
        return {"query": query}

    with pytest.raises(ToolValidationError, match="query"):
        registry.dispatch("needs_query", {})


def test_registry_reports_which_tools_write(registry: Registry) -> None:
    registry.tool(
        name="reader",
        description="Read.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    )(lambda: {})
    registry.tool(
        name="writer",
        description="Write.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        writes=True,
    )(lambda: {})

    assert registry.writes("writer") is True
    assert registry.writes("reader") is False


def test_a_tool_name_cannot_be_registered_twice(registry: Registry) -> None:
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    registry.tool(name="dup", description="First.", input_schema=schema)(lambda: {})

    with pytest.raises(ToolValidationError, match="dup"):
        registry.tool(name="dup", description="Second.", input_schema=schema)(lambda: {})
