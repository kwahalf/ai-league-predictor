"""The shipped reference tool.

One tool ships so there is a worked example to copy; the rest are yours. The REST
call is injected, so these tests need no database.
"""

import pytest

from src.agent import tools
from src.agent.registry import registry


def test_the_reference_tool_is_registered() -> None:
    assert registry.has("list_entity_types")


def test_it_is_declared_as_a_read_tool() -> None:
    assert registry.writes("list_entity_types") is False


def test_its_declaration_is_closed_and_argument_free() -> None:
    [declaration] = registry.declarations(["list_entity_types"])

    assert declaration["input_schema"]["additionalProperties"] is False
    assert declaration["input_schema"].get("required", []) == []
    assert declaration["description"]


def test_it_summarises_the_entity_types_present(monkeypatch) -> None:
    monkeypatch.setattr(
        tools,
        "request",
        lambda path, data=None, method="POST", prefer="": [
            {"entity_type": "portfolio"},
            {"entity_type": "portfolio"},
            {"entity_type": "holding"},
        ],
    )

    result = registry.dispatch("list_entity_types", {})

    assert result == {
        "entity_types": [
            {"entity_type": "holding", "count": 1},
            {"entity_type": "portfolio", "count": 2},
        ],
        "total_entities": 3,
    }


def test_it_reports_an_empty_database_plainly(monkeypatch) -> None:
    monkeypatch.setattr(
        tools, "request", lambda path, data=None, method="POST", prefer="": []
    )

    result = registry.dispatch("list_entity_types", {})

    assert result == {"entity_types": [], "total_entities": 0}


def test_it_reads_rather_than_writes(monkeypatch) -> None:
    seen = {}

    def fake_request(path, data=None, method="POST", prefer=""):
        seen["path"] = path
        seen["method"] = method
        seen["data"] = data
        return []

    monkeypatch.setattr(tools, "request", fake_request)

    registry.dispatch("list_entity_types", {})

    assert seen["method"] == "GET"
    assert seen["data"] is None
    assert seen["path"].startswith("entities?")


# ── The write tool ────────────────────────────────────────────────────────


def test_the_write_tool_is_declared_as_writing() -> None:
    # This is what obliges an agent config to pair it with an approval step.
    assert registry.writes("record_note") is True


def test_the_write_tool_requires_its_text(monkeypatch) -> None:
    from src.agent.registry import ToolValidationError

    try:
        registry.dispatch("record_note", {})
    except ToolValidationError as exc:
        assert "note" in str(exc)
    else:
        raise AssertionError("expected the missing argument to be refused")


def test_the_write_tool_creates_a_note_entity(monkeypatch) -> None:
    sent = {}

    def fake_request(path, data=None, method="POST", prefer=""):
        sent["path"] = path
        sent["data"] = data
        return {"entity_id": "11111111-1111-1111-1111-111111111111", "success": True}

    monkeypatch.setattr(tools, "request", fake_request)

    result = registry.dispatch("record_note", {"note": "Reviewed the portfolio."})

    assert sent["path"] == "rpc/create_entity_with_version"
    assert sent["data"]["p_entity_type"] == "note"
    assert sent["data"]["p_data"]["note"] == "Reviewed the portfolio."
    assert result["entity_id"] == "11111111-1111-1111-1111-111111111111"


def test_the_write_tool_refuses_an_empty_note(monkeypatch) -> None:
    monkeypatch.setattr(
        tools, "request", lambda *a, **k: pytest.fail("must not write an empty note")
    )

    result = registry.dispatch("record_note", {"note": "   "})

    assert "error" in result
