"""Shared test fixtures for ThingWire gateway tests."""

import pytest

from gateway.config import GatewayConfig


@pytest.fixture
def test_config() -> GatewayConfig:
    """Return a test configuration with defaults."""
    return GatewayConfig(
        mqtt_broker="localhost",
        mqtt_port=1883,
        audit_db_path=":memory:",
        log_level="DEBUG",
    )


@pytest.fixture
def sample_td_json() -> str:
    """Return the spec-defined WoT Thing Description as JSON string."""
    import json

    return json.dumps(
        {
            "@context": "https://www.w3.org/2019/wot/td/v1.1",
            "@type": "Thing",
            "id": "urn:thingwire:device:abc123",
            "title": "ThingWire Demo Device",
            "description": "ESP32-S3 with temperature, humidity, motion sensors and relay actuator",
            "securityDefinitions": {"nosec_sc": {"scheme": "nosec"}},
            "security": ["nosec_sc"],
            "properties": {
                "temperature": {
                    "type": "number",
                    "unit": "celsius",
                    "readOnly": True,
                    "description": "Current temperature reading from DHT22 sensor",
                    "forms": [
                        {
                            "href": "mqtt://broker/thingwire/abc123/telemetry",
                            "op": "observeproperty",
                        }
                    ],
                },
                "humidity": {
                    "type": "number",
                    "unit": "percent",
                    "readOnly": True,
                    "description": "Current humidity reading from DHT22 sensor",
                    "forms": [
                        {
                            "href": "mqtt://broker/thingwire/abc123/telemetry",
                            "op": "observeproperty",
                        }
                    ],
                },
                "motion": {
                    "type": "boolean",
                    "readOnly": True,
                    "description": "PIR motion sensor \u2014 true when motion detected",
                    "forms": [
                        {
                            "href": "mqtt://broker/thingwire/abc123/telemetry",
                            "op": "observeproperty",
                        }
                    ],
                },
            },
            "actions": {
                "setRelay": {
                    "title": "Control relay",
                    "description": "Turn relay on or off. Controls a physical device connected to the relay.",
                    "input": {
                        "type": "object",
                        "properties": {
                            "state": {
                                "type": "boolean",
                                "description": "true = on, false = off",
                            }
                        },
                        "required": ["state"],
                    },
                    "safe": False,
                    "idempotent": True,
                    "forms": [
                        {
                            "href": "mqtt://broker/thingwire/abc123/command",
                            "op": "invokeaction",
                        }
                    ],
                }
            },
        }
    )
