"""Tests for Home Assistant MQTT discovery."""

import json
from unittest.mock import MagicMock

from thingwire.ha_discovery import publish_ha_discovery
from thingwire.td_loader import parse_thing_description


def test_publishes_discovery_for_all_entities(sample_td_json: str) -> None:
    """Should publish 4 discovery messages: 3 sensors + 1 switch."""
    td = parse_thing_description(sample_td_json)
    client = MagicMock()

    count = publish_ha_discovery(client, td, "demo-001")

    assert count == 4
    assert client.publish.call_count == 4


def test_sensor_discovery_format(sample_td_json: str) -> None:
    """Temperature sensor should have correct HA discovery config."""
    td = parse_thing_description(sample_td_json)
    client = MagicMock()

    publish_ha_discovery(client, td, "demo-001")

    calls = {c[0][0]: json.loads(c[0][1]) for c in client.publish.call_args_list}

    temp_topic = "homeassistant/sensor/thingwire_demo-001_temperature/config"
    assert temp_topic in calls

    config = calls[temp_topic]
    assert config["unique_id"] == "thingwire_demo-001_temperature"
    assert config["unit_of_measurement"] == "\u00b0C"
    assert config["device_class"] == "temperature"
    assert "device" in config
    assert config["device"]["manufacturer"] == "ThingWire"


def test_switch_discovery_format(sample_td_json: str) -> None:
    """setRelay action should produce a switch entity."""
    td = parse_thing_description(sample_td_json)
    client = MagicMock()

    publish_ha_discovery(client, td, "demo-001")

    calls = {c[0][0]: json.loads(c[0][1]) for c in client.publish.call_args_list}

    switch_topic = "homeassistant/switch/thingwire_demo-001_setrelay/config"
    assert switch_topic in calls

    config = calls[switch_topic]
    assert "command_topic" in config
    assert "thingwire/demo-001/command" in config["command_topic"]


def test_binary_sensor_for_motion(sample_td_json: str) -> None:
    """Motion property (boolean) should be a binary_sensor."""
    td = parse_thing_description(sample_td_json)
    client = MagicMock()

    publish_ha_discovery(client, td, "demo-001")

    calls = {c[0][0]: json.loads(c[0][1]) for c in client.publish.call_args_list}

    motion_topic = "homeassistant/binary_sensor/thingwire_demo-001_motion/config"
    assert motion_topic in calls
