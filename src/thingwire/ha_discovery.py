"""Home Assistant MQTT Discovery — publish HA-compatible discovery messages.

When enabled, ThingWire publishes HA MQTT discovery config for each device
property and action, making ThingWire devices show up automatically in
Home Assistant without any custom integration.

Ref: https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery
"""

import json
import logging
from typing import Any

import paho.mqtt.client as mqtt

from thingwire.td_loader import ThingDescription

logger = logging.getLogger(__name__)

HA_DISCOVERY_PREFIX = "homeassistant"

# Map WoT TD property types to HA component types
_PROPERTY_TYPE_MAP: dict[str, str] = {
    "number": "sensor",
    "boolean": "binary_sensor",
    "string": "sensor",
    "integer": "sensor",
}

# Map common units to HA device classes
_UNIT_DEVICE_CLASS: dict[str, str] = {
    "celsius": "temperature",
    "fahrenheit": "temperature",
    "percent": "humidity",
    "hPa": "pressure",
    "lux": "illuminance",
}


def _slugify(name: str) -> str:
    """Simple slug for HA object IDs."""
    return name.lower().replace(" ", "_").replace("-", "_")


def _build_device_block(td: ThingDescription, device_id: str) -> dict[str, Any]:
    """Build the HA device block shared across all entities."""
    return {
        "identifiers": [f"thingwire_{device_id}"],
        "name": td.title,
        "manufacturer": "ThingWire",
        "model": "ESP32-S3",
        "sw_version": "0.3.0",
    }


def publish_ha_discovery(
    client: mqtt.Client,
    td: ThingDescription,
    device_id: str,
    topic_prefix: str = "thingwire",
) -> int:
    """Publish HA MQTT discovery config for all properties and actions.

    Returns the number of discovery messages published.
    """
    device_block = _build_device_block(td, device_id)
    count = 0

    # Properties → sensors / binary_sensors
    for prop_name, prop in td.properties.items():
        component = _PROPERTY_TYPE_MAP.get(prop.type, "sensor")
        object_id = f"thingwire_{device_id}_{_slugify(prop_name)}"
        discovery_topic = f"{HA_DISCOVERY_PREFIX}/{component}/{object_id}/config"

        config: dict[str, Any] = {
            "name": prop_name.replace("_", " ").title(),
            "unique_id": object_id,
            "state_topic": f"{topic_prefix}/{device_id}/telemetry",
            "device": device_block,
        }

        # Extract value from nested telemetry JSON
        # Telemetry format: {"readings": {"temp1": {"value": 23.5, "unit": "celsius"}}}
        # We need a value_template that maps property name to the telemetry key
        telemetry_key = _guess_telemetry_key(prop_name)
        config["value_template"] = (
            f"{{{{ value_json.readings.{telemetry_key}.value }}}}"
        )

        if prop.unit:
            config["unit_of_measurement"] = _normalize_unit(prop.unit)
            device_class = _UNIT_DEVICE_CLASS.get(prop.unit)
            if device_class:
                config["device_class"] = device_class

        if component == "binary_sensor":
            config["payload_on"] = "true"
            config["payload_off"] = "false"
            config["value_template"] = (
                f"{{{{ value_json.readings.{telemetry_key}.value }}}}"
            )

        client.publish(discovery_topic, json.dumps(config), retain=True)
        logger.info("Published HA discovery: %s → %s", prop_name, discovery_topic)
        count += 1

    # Actions → switches (for boolean set actions)
    for action_name, action in td.actions.items():
        if not action.input:
            continue

        # Only create switch entities for boolean state actions
        has_bool_state = any(
            p.type == "boolean" for p in action.input.properties.values()
        )
        if not has_bool_state:
            continue

        object_id = f"thingwire_{device_id}_{_slugify(action_name)}"
        discovery_topic = f"{HA_DISCOVERY_PREFIX}/switch/{object_id}/config"

        config = {
            "name": action.title or action_name,
            "unique_id": object_id,
            "command_topic": f"{topic_prefix}/{device_id}/command",
            "state_topic": f"{topic_prefix}/{device_id}/status",
            "payload_on": json.dumps({
                "action_id": "ha-auto",
                "target": f"{_slugify(action_name.replace('set', '').lower())}1",
                "command": "set",
                "value": True,
            }),
            "payload_off": json.dumps({
                "action_id": "ha-auto",
                "target": f"{_slugify(action_name.replace('set', '').lower())}1",
                "command": "set",
                "value": False,
            }),
            "device": device_block,
        }

        client.publish(discovery_topic, json.dumps(config), retain=True)
        logger.info("Published HA discovery: %s → %s (switch)", action_name, discovery_topic)
        count += 1

    return count


def _guess_telemetry_key(prop_name: str) -> str:
    """Map property name to telemetry readings key.

    e.g. temperature → temp1, humidity → humidity1, motion → motion1
    """
    mapping: dict[str, str] = {
        "temperature": "temp1",
        "humidity": "humidity1",
        "motion": "motion1",
        "pressure": "pressure1",
        "moisture": "moisture1",
        "angle": "angle1",
    }
    return mapping.get(prop_name.lower(), f"{prop_name}1")


def _normalize_unit(unit: str) -> str:
    """Normalize WoT TD units to HA-compatible units."""
    mapping: dict[str, str] = {
        "celsius": "\u00b0C",
        "fahrenheit": "\u00b0F",
        "percent": "%",
        "hPa": "hPa",
        "degrees": "\u00b0",
    }
    return mapping.get(unit, unit)
