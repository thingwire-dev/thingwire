"""Tests for WoT Thing Description loader and parser."""

import json

import pytest

from gateway.td_loader import ThingDescription, parse_thing_description, parse_thing_description_dict


def test_parse_valid_td(sample_td_json: str) -> None:
    """Parse the spec TD — should get 3 properties and 1 action."""
    td = parse_thing_description(sample_td_json)

    assert isinstance(td, ThingDescription)
    assert len(td.properties) == 3
    assert len(td.actions) == 1
    assert td.id == "urn:thingwire:device:abc123"
    assert td.title == "ThingWire Demo Device"


def test_property_fields(sample_td_json: str) -> None:
    """Verify temperature property has correct fields."""
    td = parse_thing_description(sample_td_json)

    temp = td.properties["temperature"]
    assert temp.type == "number"
    assert temp.unit == "celsius"
    assert temp.read_only is True
    assert "DHT22" in temp.description
    assert len(temp.forms) == 1
    assert temp.forms[0].op == "observeproperty"


def test_action_fields(sample_td_json: str) -> None:
    """Verify setRelay action has correct fields."""
    td = parse_thing_description(sample_td_json)

    relay = td.actions["setRelay"]
    assert relay.safe is False
    assert relay.idempotent is True
    assert relay.input is not None
    assert "state" in relay.input.properties
    assert relay.input.properties["state"].type == "boolean"
    assert "state" in relay.input.required


def test_parse_invalid_json() -> None:
    """Invalid JSON string should raise ValueError."""
    with pytest.raises(ValueError, match="Invalid JSON"):
        parse_thing_description("not valid json {{{")


def test_parse_missing_required_field() -> None:
    """Missing @context should raise ValueError."""
    data = {
        "@type": "Thing",
        "id": "urn:test",
        "title": "Test",
        "description": "Test",
        "securityDefinitions": {"nosec_sc": {"scheme": "nosec"}},
        "security": ["nosec_sc"],
    }
    with pytest.raises(ValueError, match="Invalid Thing Description"):
        parse_thing_description(json.dumps(data))


def test_parse_empty_properties() -> None:
    """TD with no properties or actions should parse fine."""
    data = {
        "@context": "https://www.w3.org/2019/wot/td/v1.1",
        "@type": "Thing",
        "id": "urn:thingwire:device:empty",
        "title": "Empty Device",
        "description": "A device with no sensors",
        "securityDefinitions": {"nosec_sc": {"scheme": "nosec"}},
        "security": ["nosec_sc"],
    }
    td = parse_thing_description(json.dumps(data))
    assert len(td.properties) == 0
    assert len(td.actions) == 0


def test_parse_from_dict(sample_td_json: str) -> None:
    """Test parse_thing_description_dict directly."""
    data = json.loads(sample_td_json)
    td = parse_thing_description_dict(data)

    assert td.id == "urn:thingwire:device:abc123"
    assert len(td.properties) == 3
    assert len(td.actions) == 1
