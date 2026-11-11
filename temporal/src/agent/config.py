"""Agent configuration — the only file a domain edits to change what an agent may do.

The rules enforced here are the privilege boundary, and each one fails the run at
its first step naming the offender rather than degrading quietly:

1. NARROW ONLY. Every granted tool must already exist in the registry. A config
   chooses among the framework's tools; it cannot define one, describe its
   behaviour, or name the activity behind it.
2. NO ESCALATION BY OMISSION. A tool the registry marks `writes=True` must carry
   `requires_approval: true`, unless the config sets `allow_unapproved_writes`.
   Forgetting the flag cannot silently grant an unattended write.
3. A CLOSED ANSWER SCHEMA, because that is what a strict output format requires.

The resulting `AgentConfig` is a plain dataclass of primitives so it can cross
the Temporal activity boundary and be replayed from history.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .registry import Registry
from .registry import registry as default_registry

DEFAULT_AGENTS_DIR = Path(__file__).resolve().parent.parent.parent / "agents"
DEFAULT_TOOL_TIMEOUT_SECONDS = 60
DEFAULT_MAX_TOOL_ROUNDS = 6


class ConfigError(Exception):
    """An agent configuration is missing, malformed, or asks for too much."""


@dataclass(frozen=True)
class ToolGrant:
    name: str
    requires_approval: bool = False
    timeout_seconds: int | None = None


@dataclass(frozen=True)
class AgentConfig:
    name: str
    provider: str
    model: str
    system_prompt: str
    answer_schema: dict[str, Any]
    tools: list[ToolGrant] = field(default_factory=list)
    max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS
    max_tokens: int = 4096
    allow_unapproved_writes: bool = False
    default_tool_timeout_seconds: int = DEFAULT_TOOL_TIMEOUT_SECONDS

    @property
    def allowed_tools(self) -> set[str]:
        return {t.name for t in self.tools}

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self.tools]

    def requires_approval(self, tool_name: str) -> bool:
        for grant in self.tools:
            if grant.name == tool_name:
                return grant.requires_approval
        return False

    def timeout_seconds_for(self, tool_name: str) -> int:
        for grant in self.tools:
            if grant.name == tool_name and grant.timeout_seconds:
                return grant.timeout_seconds
        return self.default_tool_timeout_seconds


def load_agent_config(
    name: str,
    *,
    agents_dir: Path | str | None = None,
    registry: Registry | None = None,
) -> AgentConfig:
    directory = Path(agents_dir) if agents_dir else DEFAULT_AGENTS_DIR
    reg = registry if registry is not None else default_registry

    path = directory / f"{name}.yaml"
    if not path.is_file():
        raise ConfigError(
            f'No configuration for agent "{name}": expected {path}. '
            f"Available: {_available(directory) or '(none)'}"
        )

    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f'Agent "{name}" has invalid YAML: {exc}') from exc

    if not isinstance(raw, dict):
        raise ConfigError(f'Agent "{name}" must be a YAML mapping')

    declared_name = raw.get("name", name)
    if declared_name != name:
        # Otherwise a run asks for one agent and gets another's prompt.
        raise ConfigError(
            f'Agent file {path.name} declares name "{declared_name}", '
            f'which does not match its filename "{name}"'
        )

    tools = _parse_tools(name, raw.get("tools"))
    allow_unapproved_writes = bool(raw.get("allow_unapproved_writes", False))

    _check_tools_exist(name, tools, reg)
    if not allow_unapproved_writes:
        _check_writes_require_approval(name, tools, reg)

    answer_schema = raw.get("answer_schema")
    _check_answer_schema(name, answer_schema)

    system_prompt = raw.get("system_prompt")
    if not isinstance(system_prompt, str) or not system_prompt.strip():
        raise ConfigError(f'Agent "{name}" needs a non-empty system_prompt')

    return AgentConfig(
        name=name,
        # AGENT_PROVIDER wins over the file so one shipped config runs against
        # the fake provider on a machine with no credentials, and against a real
        # provider in a deployment, without editing or duplicating the file.
        #
        # The default is `litellm`: an agent that names no provider goes through the
        # proxy, which is where model choice, spend caps and rate limits are
        # configured. Naming a provider in the YAML is then a deliberate act of
        # bypassing that, rather than the path of least resistance.
        provider=os.environ.get("AGENT_PROVIDER") or str(raw.get("provider", "litellm")),
        model=str(raw.get("model", "")),
        system_prompt=system_prompt,
        answer_schema=answer_schema,
        tools=tools,
        max_tool_rounds=int(raw.get("max_tool_rounds", DEFAULT_MAX_TOOL_ROUNDS)),
        max_tokens=int(raw.get("max_tokens", 4096)),
        allow_unapproved_writes=allow_unapproved_writes,
        default_tool_timeout_seconds=int(
            raw.get("default_tool_timeout_seconds", DEFAULT_TOOL_TIMEOUT_SECONDS)
        ),
    )


def _available(directory: Path) -> str:
    if not directory.is_dir():
        return ""
    return ", ".join(sorted(p.stem for p in directory.glob("*.yaml")))


def _parse_tools(agent: str, raw_tools: Any) -> list[ToolGrant]:
    if not isinstance(raw_tools, list) or not raw_tools:
        raise ConfigError(
            f'Agent "{agent}" must grant at least one tool. An agent with no tools '
            "cannot do anything, so it is written as no agent rather than an empty list."
        )

    grants: list[ToolGrant] = []
    for entry in raw_tools:
        if isinstance(entry, str):
            grants.append(ToolGrant(name=entry))
            continue
        if not isinstance(entry, dict) or "name" not in entry:
            raise ConfigError(f'Agent "{agent}" has a tool entry without a name: {entry!r}')
        unknown_keys = set(entry) - {"name", "requires_approval", "timeout_seconds"}
        if unknown_keys:
            raise ConfigError(
                f'Agent "{agent}" tool "{entry["name"]}" has unsupported key(s): '
                f'{", ".join(sorted(unknown_keys))}. A config declares which tools are '
                "granted, never how they behave."
            )
        grants.append(
            ToolGrant(
                name=str(entry["name"]),
                requires_approval=bool(entry.get("requires_approval", False)),
                timeout_seconds=(
                    int(entry["timeout_seconds"]) if entry.get("timeout_seconds") else None
                ),
            )
        )

    duplicates = {g.name for g in grants if grants.count(g) > 1}
    if len({g.name for g in grants}) != len(grants):
        raise ConfigError(
            f'Agent "{agent}" grants the same tool twice: {", ".join(sorted(duplicates)) or "?"}'
        )
    return grants


def _check_tools_exist(agent: str, tools: list[ToolGrant], reg: Registry) -> None:
    for grant in tools:
        if not reg.has(grant.name):
            raise ConfigError(
                f'Agent "{agent}" grants tool "{grant.name}", which is not in the registry. '
                f"A config may narrow the framework's tools and may not add to them. "
                f'Registered tools: {", ".join(reg.names()) or "(none)"}'
            )


def _check_writes_require_approval(agent: str, tools: list[ToolGrant], reg: Registry) -> None:
    for grant in tools:
        if reg.writes(grant.name) and not grant.requires_approval:
            raise ConfigError(
                f'Agent "{agent}" grants writing tool "{grant.name}" without '
                "requires_approval: true. Set it, or set allow_unapproved_writes: true "
                "on the agent to accept unattended writes deliberately."
            )


def _check_answer_schema(agent: str, schema: Any) -> None:
    if not isinstance(schema, dict):
        raise ConfigError(f'Agent "{agent}" needs an answer_schema object')
    if schema.get("type") != "object":
        raise ConfigError(f'Agent "{agent}" answer_schema must have type "object"')
    if schema.get("additionalProperties") is not False:
        raise ConfigError(
            f'Agent "{agent}" answer_schema must set additionalProperties: false, '
            "which is what a strict output format requires."
        )
