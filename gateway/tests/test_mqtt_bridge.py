"""Tests for MQTT bridge — unit tests with mocked MQTT client."""

import json
from unittest.mock import MagicMock

from gateway.config import GatewayConfig
from gateway.mqtt_bridge import MqttBridge

SAMPLE_TD = {
    "@context": "https://www.w3.org/2019/wot/td/v1.1",
    "@type": "Thing",
    "id": "urn:thingwire:device:test-001",
    "title": "Test Device",
    "description": "A test device",
    "securityDefinitions": {"nosec_sc": {"scheme": "nosec"}},
    "security": ["nosec_sc"],
    "properties": {},
    "actions": {},
}

SAMPLE_TELEMETRY = {
    "timestamp": "2026-04-01T10:00:00Z",
    "readings": {
        "temp1": {"value": 23.5, "unit": "celsius"},
        "humidity1": {"value": 45.0, "unit": "percent"},
        "motion1": {"value": False, "unit": "boolean"},
    },
}


def _make_bridge() -> MqttBridge:
    config = GatewayConfig(mqtt_broker="localhost", mqtt_port=1883)
    return MqttBridge(config)


def test_handle_td() -> None:
    """TD message should register the device."""
    bridge = _make_bridge()
    bridge._handle_td("test-001", json.dumps(SAMPLE_TD).encode())

    assert "test-001" in bridge.get_devices()
    td = bridge.get_td("test-001")
    assert td is not None
    assert td.title == "Test Device"


def test_handle_telemetry() -> None:
    """Telemetry message should store latest readings."""
    bridge = _make_bridge()
    bridge._handle_telemetry("test-001", json.dumps(SAMPLE_TELEMETRY).encode())

    reading = bridge.get_latest_reading("test-001", "temp1")
    assert reading["value"] == 23.5
    assert reading["unit"] == "celsius"


def test_get_latest_reading_no_data() -> None:
    """Should return error when no telemetry available."""
    bridge = _make_bridge()
    result = bridge.get_latest_reading("unknown-device", "temperature")
    assert "error" in result


def test_handle_status() -> None:
    """Status messages should update device status."""
    bridge = _make_bridge()
    bridge._handle_status("test-001", b"online")
    assert bridge.get_device_status("test-001") == "online"

    bridge._handle_status("test-001", b"offline")
    assert bridge.get_device_status("test-001") == "offline"


def test_handle_invalid_td() -> None:
    """Invalid TD JSON should not crash, device should not be registered."""
    bridge = _make_bridge()
    bridge._handle_td("bad-device", b"not valid json")
    assert "bad-device" not in bridge.get_devices()


def test_handle_invalid_telemetry() -> None:
    """Invalid telemetry JSON should not crash."""
    bridge = _make_bridge()
    bridge._handle_telemetry("test-001", b"{{bad json")
    result = bridge.get_latest_reading("test-001", "temperature")
    assert "error" in result


def test_message_routing() -> None:
    """on_message should route to correct handler based on topic."""
    bridge = _make_bridge()
    msg = MagicMock()
    msg.topic = "thingwire/test-001/td"
    msg.payload = json.dumps(SAMPLE_TD).encode()

    bridge._on_message(None, None, msg)
    assert "test-001" in bridge.get_devices()


def test_is_device_online() -> None:
    """is_device_online should reflect status."""
    bridge = _make_bridge()
    assert bridge.is_device_online("test-001") is False

    bridge._handle_status("test-001", b"online")
    assert bridge.is_device_online("test-001") is True


def test_unknown_device_status() -> None:
    """Unknown device should return 'unknown' status."""
    bridge = _make_bridge()
    assert bridge.get_device_status("nonexistent") == "unknown"
