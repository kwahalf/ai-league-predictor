"""Tool registry — the one place a tool is both declared and implemented.

Two callers share this registry, which is why it exists at all:

* `declarations(names)` renders what the model is offered.
* `dispatch(name, args)` runs what the model asked for.

Because both read the same entry, a tool cannot be advertised without an
implementation, or implemented without being advertised. Model output is
untrusted input: `dispatch` validates arguments against the tool's own schema
before the function is called.

Nothing here knows about Temporal, Supabase or a model provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

ToolFn = Callable[..., Any]


class ToolNotFound(Exception):
    """A tool name was requested that no one registered."""


class ToolValidationError(Exception):
    """A registration is malformed, or arguments do not match a tool's schema."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    writes: bool
    fn: ToolFn


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    # ── Registration ──────────────────────────────────────────────────────

    def tool(
        self,
        *,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        writes: bool = False,
    ) -> Callable[[ToolFn], ToolFn]:
        """Register a function as a tool the model may call.

        `writes=True` marks a tool with side effects. Agent configs must pair a
        writing tool with `requires_approval` unless they opt out explicitly, so
        this flag is a privilege declaration and not documentation.
        """

        def decorate(fn: ToolFn) -> ToolFn:
            if name in self._tools:
                raise ToolValidationError(f'Tool "{name}" is already registered')
            _check_schema(name, input_schema)
            self._tools[name] = ToolSpec(
                name=name,
                description=description,
                input_schema=input_schema,
                writes=writes,
                fn=fn,
            )
            return fn

        return decorate

    # ── Reading ───────────────────────────────────────────────────────────

    def names(self) -> list[str]:
        return sorted(self._tools)

    def has(self, name: str) -> bool:
        return name in self._tools

    def writes(self, name: str) -> bool:
        return self._spec(name).writes

    def declarations(self, names: Iterable[str]) -> list[dict[str, Any]]:
        """Model-facing declarations for exactly the named tools, in order."""
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
            }
            for spec in (self._spec(n) for n in names)
        ]

    # ── Execution ─────────────────────────────────────────────────────────

    def dispatch(self, name: str, arguments: dict[str, Any]) -> Any:
        spec = self._spec(name)
        validate_arguments(spec.input_schema, arguments, tool_name=name)
        return spec.fn(**arguments)

    def _spec(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFound(
                f'Tool "{name}" is not in the registry. Registered tools: '
                f'{", ".join(self.names()) or "(none)"}'
            ) from None


# ── Argument validation ───────────────────────────────────────────────────
#
# A deliberately small JSON Schema subset — object/required/additionalProperties
# plus scalar and container types — rather than a schema library. Tool arguments
# are shallow by design, and a tool needing more validation than this should
# check its own inputs and say why in its error.

_JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _check_schema(tool_name: str, schema: dict[str, Any]) -> None:
    if schema.get("type") != "object":
        raise ToolValidationError(f'Tool "{tool_name}" input_schema must have type "object"')
    if not isinstance(schema.get("properties", {}), dict):
        raise ToolValidationError(f'Tool "{tool_name}" input_schema properties must be an object')


def validate_arguments(
    schema: dict[str, Any], arguments: dict[str, Any], *, tool_name: str
) -> None:
    if not isinstance(arguments, dict):
        raise ToolValidationError(f'Tool "{tool_name}" arguments must be an object')

    properties: dict[str, Any] = schema.get("properties", {})

    for field in schema.get("required", []):
        if field not in arguments:
            raise ToolValidationError(f'Tool "{tool_name}" requires argument "{field}"')

    if schema.get("additionalProperties") is False:
        unexpected = sorted(set(arguments) - set(properties))
        if unexpected:
            raise ToolValidationError(
                f'Tool "{tool_name}" received unexpected argument(s): {", ".join(unexpected)}'
            )

    for field, value in arguments.items():
        declared = properties.get(field, {}).get("type")
        expected = _JSON_TYPES.get(declared) if declared else None
        if expected is None or value is None:
            continue
        # bool is a subclass of int in Python; an integer field must not accept True.
        if declared in ("integer", "number") and isinstance(value, bool):
            raise ToolValidationError(
                f'Tool "{tool_name}" argument "{field}" must be {declared}, got boolean'
            )
        if not isinstance(value, expected):
            raise ToolValidationError(
                f'Tool "{tool_name}" argument "{field}" must be {declared}, '
                f"got {type(value).__name__}"
            )


# The registry the worker uses. Tools register on import of src.agent.tools.
registry = Registry()
tool = registry.tool
