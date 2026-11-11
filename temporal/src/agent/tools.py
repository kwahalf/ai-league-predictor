"""Domain tools — THIS IS THE FILE YOU EDIT.

One reference tool ships. Copy its shape for your own domain:

    @tool(
        name="search_entities",                  # what the model calls
        description="Find entities by name.",    # how the model decides to call it
        input_schema={...},                      # what the model may pass
        writes=False,                            # True if it changes anything
    )
    def search_entities(query: str) -> dict:
        return {"entities": request(f"entities?...", method="GET")}

Four rules the framework enforces around you, so you do not have to:

* A tool is only callable by an agent whose YAML names it, and a YAML can only
  name tools that exist here.
* Arguments are validated against `input_schema` before your function runs, so
  you can trust the values you were given.
* `writes=True` obliges an agent config to pair the tool with an approval step.
* Returned values are serialised and truncated before they reach the model, so
  returning a large result degrades the answer rather than breaking the run.

Keep tools small and boring. A tool that returns the rows it was asked for beats
a tool that tries to be clever, because the model is the clever part.
"""

from __future__ import annotations

from collections import Counter

from ..activities.client import request
from .registry import tool


@tool(
    name="list_entity_types",
    description=(
        "List the entity types present in the database with a count of each. "
        "Use this first to discover what kinds of records exist before asking "
        "about a specific one."
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    writes=False,
)
def list_entity_types() -> dict:
    """Summarise what is in the entity table.

    Deliberately the dullest useful tool: no arguments to get wrong, no writes,
    and meaningful in any domain built on this template's schema — so it is a
    worked example rather than a feature.
    """
    rows = request("entities?select=entity_type", method="GET") or []
    counts = Counter(str(row.get("entity_type")) for row in rows)
    return {
        "entity_types": [
            {"entity_type": name, "count": count} for name, count in sorted(counts.items())
        ],
        "total_entities": len(rows),
    }


@tool(
    name="record_note",
    description=(
        "Record a short note as a note entity, so an observation is kept for "
        "someone to read later. Use it only when the user asks for something to "
        "be written down."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "note": {
                "type": "string",
                "description": "The note to record, in one or two sentences.",
            }
        },
        "required": ["note"],
        "additionalProperties": False,
    },
    writes=True,
)
def record_note(note: str) -> dict:
    """The write-tool example, and the reason the approval path is demonstrable.

    `writes=True` is the important line: it obliges any agent config granting
    this tool to pair it with `requires_approval: true`, so the run pauses and a
    human decides before anything is written. Copy this shape for your own write
    tools and the same protection applies to them.

    Note that a tool still validates what a schema cannot: the schema says the
    note is a string, and this says a blank string is not a note. Returning an
    error rather than raising lets the model read the problem and try again.
    """
    if not note.strip():
        return {"error": "A note cannot be empty. Say what you observed."}

    result = request(
        "rpc/create_entity_with_version",
        {"p_entity_type": "note", "p_data": {"note": note.strip()}},
    )
    return {"entity_id": (result or {}).get("entity_id"), "recorded": True}
