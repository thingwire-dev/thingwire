"""Tests for WoT TD → MCP tool compiler."""

import json

from thingwire.td_loader import parse_thing_description
from thingwire.tool_compiler import CompiledTool, compile_tools


def test_compile_produces_four_tools(sample_td_json: str) -> None:
    """Spec TD should produce 4 tools: 3 read + 1 action."""
    td = parse_thing_description(sample_td_json)
    tools = compile_tools(td)

    assert len(tools) == 4
    read_tools = [t for t in tools if t.tool_type == "read"]
    action_tools = [t for t in tools if t.tool_type == "action"]
    assert len(read_tools) == 3
    assert len(action_tools) == 1


def test_tool_names(sample_td_json: str) -> None:
    """Tool names should be slugified: read_temperature, read_humidity, read_motion, do_set_relay."""
    td = parse_thing_description(sample_td_json)
    tools = compile_tools(td)
    names = {t.name for t in tools}

    assert names == {"read_temperature", "read_humidity", "read_motion", "do_set_relay"}


def test_dangerous_action_description(sample_td_json: str) -> None:
    """do_set_relay description should start with warning emoji."""
    td = parse_thing_description(sample_td_json)
    tools = compile_tools(td)
    relay = next(t for t in tools if t.name == "do_set_relay")

    assert relay.description.startswith("\u26a0\ufe0f")
    assert relay.safe is False


def test_read_tool_has_no_params(sample_td_json: str) -> None:
    """Read tools should have no parameters."""
    td = parse_thing_description(sample_td_json)
    tools = compile_tools(td)
    temp = next(t for t in tools if t.name == "read_temperature")

    assert temp.parameters == []
    assert temp.safe is True


def test_action_tool_has_params(sample_td_json: str) -> None:
    """do_set_relay should have a 'state' parameter."""
    td = parse_thing_description(sample_td_json)
    tools = compile_tools(td)
    relay = next(t for t in tools if t.name == "do_set_relay")

    assert len(relay.parameters) == 1
    assert relay.parameters[0].name == "state"
    assert relay.parameters[0].type == "boolean"
    assert relay.parameters[0].required is True


def test_device_id_extracted(sample_td_json: str) -> None:
    """Device ID should come from the TD id field."""
    td = parse_thing_description(sample_td_json)
    tools = compile_tools(td)

    for tool in tools:
        assert tool.device_id == "urn:thingwire:device:abc123"


def test_empty_td_produces_no_tools() -> None:
    """TD with no properties or actions should produce no tools."""
    td_json = json.dumps({
        "@context": "https://www.w3.org/2019/wot/td/v1.1",
        "@type": "Thing",
        "id": "urn:thingwire:device:empty",
        "title": "Empty",
        "description": "No sensors",
        "securityDefinitions": {"nosec_sc": {"scheme": "nosec"}},
        "security": ["nosec_sc"],
    })
    from thingwire.td_loader import parse_thing_description as parse

    td = parse(td_json)
    tools = compile_tools(td)
    assert tools == []
