"""Agent configuration: a domain may narrow the framework's tools, never widen them."""

from pathlib import Path

import pytest

from src.agent.config import ConfigError, load_agent_config
from src.agent.registry import Registry

SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


@pytest.fixture
def registry() -> Registry:
    reg = Registry()
    reg.tool(name="read_thing", description="Read.", input_schema=SCHEMA)(lambda: {})
    reg.tool(name="other_read", description="Read.", input_schema=SCHEMA)(lambda: {})
    reg.tool(name="write_thing", description="Write.", input_schema=SCHEMA, writes=True)(
        lambda: {}
    )
    return reg


def write_config(directory: Path, body: str, name: str = "demo") -> Path:
    path = directory / f"{name}.yaml"
    path.write_text(body)
    return path


VALID = """
name: demo
provider: fake
model: test-model
max_tool_rounds: 4
system_prompt: You are a demo agent.
answer_schema:
  type: object
  properties:
    summary: { type: string }
  required: [summary]
  additionalProperties: false
tools:
  - name: read_thing
  - name: write_thing
    requires_approval: true
    timeout_seconds: 45
"""


def test_loads_the_declared_tools_and_nothing_else(tmp_path: Path, registry: Registry) -> None:
    write_config(tmp_path, VALID)

    cfg = load_agent_config("demo", agents_dir=tmp_path, registry=registry)

    assert cfg.allowed_tools == {"read_thing", "write_thing"}
    assert "other_read" not in cfg.allowed_tools


def test_config_values_are_exposed(tmp_path: Path, registry: Registry) -> None:
    write_config(tmp_path, VALID)

    cfg = load_agent_config("demo", agents_dir=tmp_path, registry=registry)

    assert cfg.provider == "fake"
    assert cfg.model == "test-model"
    assert cfg.max_tool_rounds == 4
    assert cfg.system_prompt.strip() == "You are a demo agent."


def test_a_tool_outside_the_registry_is_refused_by_name(
    tmp_path: Path, registry: Registry
) -> None:
    write_config(
        tmp_path,
        VALID.replace("  - name: read_thing", "  - name: invented_tool"),
    )

    with pytest.raises(ConfigError, match="invented_tool"):
        load_agent_config("demo", agents_dir=tmp_path, registry=registry)


def test_a_write_tool_without_approval_is_refused_by_name(
    tmp_path: Path, registry: Registry
) -> None:
    write_config(
        tmp_path,
        VALID.replace("    requires_approval: true\n", ""),
    )

    with pytest.raises(ConfigError, match="write_thing"):
        load_agent_config("demo", agents_dir=tmp_path, registry=registry)


def test_unapproved_writes_can_be_opted_into_explicitly(
    tmp_path: Path, registry: Registry
) -> None:
    write_config(
        tmp_path,
        VALID.replace("    requires_approval: true\n", "")
        + "allow_unapproved_writes: true\n",
    )

    cfg = load_agent_config("demo", agents_dir=tmp_path, registry=registry)

    assert cfg.requires_approval("write_thing") is False


def test_approval_is_per_tool(tmp_path: Path, registry: Registry) -> None:
    write_config(tmp_path, VALID)

    cfg = load_agent_config("demo", agents_dir=tmp_path, registry=registry)

    assert cfg.requires_approval("write_thing") is True
    assert cfg.requires_approval("read_thing") is False


def test_tool_timeout_falls_back_to_a_default(tmp_path: Path, registry: Registry) -> None:
    write_config(tmp_path, VALID)

    cfg = load_agent_config("demo", agents_dir=tmp_path, registry=registry)

    assert cfg.timeout_seconds_for("write_thing") == 45
    assert cfg.timeout_seconds_for("read_thing") == cfg.default_tool_timeout_seconds


def test_answer_schema_must_be_a_closed_object(tmp_path: Path, registry: Registry) -> None:
    without_closure = VALID.replace("  additionalProperties: false\n", "")
    assert "additionalProperties" not in without_closure  # guard the fixture edit itself
    write_config(tmp_path, without_closure)

    with pytest.raises(ConfigError, match="additionalProperties"):
        load_agent_config("demo", agents_dir=tmp_path, registry=registry)


def test_a_missing_agent_file_names_the_agent(tmp_path: Path, registry: Registry) -> None:
    with pytest.raises(ConfigError, match="ghost_agent"):
        load_agent_config("ghost_agent", agents_dir=tmp_path, registry=registry)


def test_an_agent_must_grant_at_least_one_tool(tmp_path: Path, registry: Registry) -> None:
    write_config(
        tmp_path,
        VALID.split("tools:")[0] + "tools: []\n",
    )

    with pytest.raises(ConfigError, match="at least one tool"):
        load_agent_config("demo", agents_dir=tmp_path, registry=registry)


def test_agent_name_must_match_its_filename(tmp_path: Path, registry: Registry) -> None:
    write_config(tmp_path, VALID.replace("name: demo", "name: something_else"))

    with pytest.raises(ConfigError, match="something_else"):
        load_agent_config("demo", agents_dir=tmp_path, registry=registry)


def test_provider_can_be_overridden_by_environment(
    tmp_path: Path, registry: Registry, monkeypatch
) -> None:
    # Lets one shipped config run against the fake provider on a machine with no
    # credentials, without editing the file or duplicating it.
    write_config(tmp_path, VALID.replace("provider: fake", "provider: claude"))
    monkeypatch.setenv("AGENT_PROVIDER", "fake")

    cfg = load_agent_config("demo", agents_dir=tmp_path, registry=registry)

    assert cfg.provider == "fake"


def test_the_configured_provider_is_used_when_no_override_is_set(
    tmp_path: Path, registry: Registry, monkeypatch
) -> None:
    write_config(tmp_path, VALID.replace("provider: fake", "provider: claude"))
    monkeypatch.delenv("AGENT_PROVIDER", raising=False)

    cfg = load_agent_config("demo", agents_dir=tmp_path, registry=registry)

    assert cfg.provider == "claude"


def test_a_config_naming_no_provider_defaults_to_the_proxy(
    tmp_path: Path, registry: Registry, monkeypatch
) -> None:
    """Omitting `provider` sends the agent through LiteLLM.

    Deliberate: model choice, spend caps and rate limits then live in one place
    rather than being decided per agent file. Naming a provider becomes a bypass of
    that, which is the right way round — the easy path should be the governed one.
    """
    write_config(tmp_path, VALID.replace("provider: fake\n", ""))
    monkeypatch.delenv("AGENT_PROVIDER", raising=False)

    cfg = load_agent_config("demo", agents_dir=tmp_path, registry=registry)

    assert cfg.provider == "litellm"
