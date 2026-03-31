"""Integration tests — full component wiring without external broker.

Tests the pipeline: TD JSON → td_loader → tool_compiler → safety → audit_log
and MQTT bridge message handling, all wired through mcp_server registration.
"""

import json

import pytest

from gateway.audit_log import AuditLog
from gateway.config import GatewayConfig
from gateway.mcp_server import create_mcp_server, register_device_tools, register_meta_tools
from gateway.mqtt_bridge import MqttBridge
from gateway.safety import SafetyError, SafetyLayer
from gateway.td_loader import parse_thing_description

SPEC_TD = {
    "@context": "https://www.w3.org/2019/wot/td/v1.1",
    "@type": "Thing",
    "id": "urn:thingwire:device:thingwire-demo-001",
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
                    "href": "mqtt://broker/thingwire/thingwire-demo-001/telemetry",
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
                    "href": "mqtt://broker/thingwire/thingwire-demo-001/telemetry",
                    "op": "observeproperty",
                }
            ],
        },
        "motion": {
            "type": "boolean",
            "readOnly": True,
            "description": "PIR motion sensor — true when motion detected",
            "forms": [
                {
                    "href": "mqtt://broker/thingwire/thingwire-demo-001/telemetry",
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
                    "state": {"type": "boolean", "description": "true = on, false = off"}
                },
                "required": ["state"],
            },
            "safe": False,
            "idempotent": True,
            "forms": [
                {
                    "href": "mqtt://broker/thingwire/thingwire-demo-001/command",
                    "op": "invokeaction",
                }
            ],
        }
    },
}

SAMPLE_TELEMETRY = {
    "timestamp": "2026-04-01T10:30:00Z",
    "readings": {
        "temp1": {"value": 23.5, "unit": "celsius"},
        "humidity1": {"value": 45.2, "unit": "percent"},
        "motion1": {"value": True, "unit": "boolean"},
    },
}


@pytest.fixture
def config() -> GatewayConfig:
    return GatewayConfig(mqtt_broker="localhost", mqtt_port=1883)


@pytest.fixture
def bridge(config: GatewayConfig) -> MqttBridge:
    """Bridge with simulated device data injected (no real MQTT connection)."""
    b = MqttBridge(config)
    # Simulate device discovery by injecting data directly
    b._devices["thingwire-demo-001"] = SPEC_TD
    b._telemetry["thingwire-demo-001"] = SAMPLE_TELEMETRY
    b._status["thingwire-demo-001"] = "online"
    return b


@pytest.fixture
def safety() -> SafetyLayer:
    layer = SafetyLayer()
    layer.register_device("thingwire-demo-001", SPEC_TD)
    return layer


@pytest.fixture
async def audit() -> AuditLog:
    log = AuditLog(":memory:")
    await log.initialize()
    yield log  # type: ignore[misc]
    await log.close()


# ── Device Discovery ──


class TestDeviceDiscovery:
    """Test that MQTT messages are correctly processed into device state."""

    def test_td_ingestion(self, config: GatewayConfig) -> None:
        """Simulated TD message populates device registry."""
        bridge = MqttBridge(config)
        bridge._handle_td("test-device", json.dumps(SPEC_TD).encode())

        assert "test-device" in bridge.get_devices()
        td = bridge.get_td("test-device")
        assert td is not None
        assert td.title == "ThingWire Demo Device"
        assert len(td.properties) == 3
        assert len(td.actions) == 1

    def test_telemetry_ingestion(self, config: GatewayConfig) -> None:
        """Simulated telemetry message populates reading cache."""
        bridge = MqttBridge(config)
        bridge._handle_telemetry("test-device", json.dumps(SAMPLE_TELEMETRY).encode())

        reading = bridge.get_latest_reading("test-device", "temp1")
        assert reading["value"] == 23.5
        assert reading["unit"] == "celsius"
        assert reading["timestamp"] == "2026-04-01T10:30:00Z"

    def test_status_tracking(self, config: GatewayConfig) -> None:
        """Online/offline status is tracked per device."""
        bridge = MqttBridge(config)
        bridge._handle_status("test-device", b"online")
        assert bridge.is_device_online("test-device") is True

        bridge._handle_status("test-device", b"offline")
        assert bridge.is_device_online("test-device") is False

    def test_malformed_td_no_crash(self, config: GatewayConfig) -> None:
        """Invalid JSON in TD message is logged, not crashed."""
        bridge = MqttBridge(config)
        bridge._handle_td("bad", b"{{not json")
        assert "bad" not in bridge.get_devices()

    def test_malformed_telemetry_no_crash(self, config: GatewayConfig) -> None:
        """Invalid JSON in telemetry message is logged, not crashed."""
        bridge = MqttBridge(config)
        bridge._handle_telemetry("bad", b"not json")
        result = bridge.get_latest_reading("bad", "temp1")
        assert "error" in result


# ── Tool Compilation Pipeline ──


class TestToolPipeline:
    """Test TD → parsed model → compiled tools → registered on MCP."""

    def test_full_pipeline_produces_correct_tools(self, bridge: MqttBridge) -> None:
        """TD from bridge → parse → compile → 4 tools with correct names."""
        from gateway.tool_compiler import compile_tools

        td = bridge.get_td("thingwire-demo-001")
        assert td is not None

        tools = compile_tools(td)
        names = {t.name for t in tools}
        assert names == {"read_temperature", "read_humidity", "read_motion", "do_set_relay"}

    def test_dangerous_action_flagged(self, bridge: MqttBridge) -> None:
        """setRelay (safe=false) compiles with danger flag."""
        from gateway.tool_compiler import compile_tools

        td = bridge.get_td("thingwire-demo-001")
        assert td is not None

        tools = compile_tools(td)
        relay = next(t for t in tools if t.name == "do_set_relay")
        assert relay.safe is False
        assert relay.description.startswith("\u26a0\ufe0f")

    @pytest.mark.asyncio
    async def test_mcp_tool_registration(
        self, bridge: MqttBridge, safety: SafetyLayer, audit: AuditLog
    ) -> None:
        """Tools are registered on MCP server from bridge data."""
        td = bridge.get_td("thingwire-demo-001")
        assert td is not None

        mcp = create_mcp_server()
        registered = register_device_tools(mcp, td, bridge, safety, audit)

        assert "read_temperature" in registered
        assert "read_humidity" in registered
        assert "read_motion" in registered
        assert "do_set_relay" in registered


# ── Safety Layer Integration ──


class TestSafetyIntegration:
    """Test safety checks in the context of real device data."""

    def test_auto_registered_permissions(self, safety: SafetyLayer) -> None:
        """Device auto-registration creates correct allowlists from TD."""
        # read_ tools should be allowed
        safety.check_permission("thingwire-demo-001", "read_temperature")
        safety.check_permission("thingwire-demo-001", "read_humidity")
        safety.check_permission("thingwire-demo-001", "do_setRelay")

    def test_unregistered_action_blocked(self, safety: SafetyLayer) -> None:
        """Actions not in the TD are blocked."""
        with pytest.raises(SafetyError, match="not in the allowlist"):
            safety.check_permission("thingwire-demo-001", "do_launchRocket")

    def test_unknown_device_blocked(self, safety: SafetyLayer) -> None:
        """Commands to unknown devices are blocked."""
        with pytest.raises(SafetyError, match="not registered"):
            safety.check_permission("unknown-device", "read_temperature")

    def test_dangerous_action_detected_from_td(self, safety: SafetyLayer) -> None:
        """setRelay is correctly flagged as dangerous from the TD."""
        assert safety.is_dangerous("thingwire-demo-001", "do_setRelay") is True
        assert safety.is_dangerous("thingwire-demo-001", "read_temperature") is False

    def test_rate_limit_with_default_config(self, safety: SafetyLayer) -> None:
        """Default rate limit (10/60s) applies to actions."""
        for _ in range(10):
            safety.check_rate_limit("thingwire-demo-001", "do_setRelay")

        with pytest.raises(SafetyError, match="Rate limit exceeded"):
            safety.check_rate_limit("thingwire-demo-001", "do_setRelay")


# ── Audit Log Integration ──


class TestAuditIntegration:
    """Test audit logging in the full pipeline context."""

    @pytest.mark.asyncio
    async def test_read_command_logged(self, audit: AuditLog) -> None:
        """Read operations are recorded in audit log."""
        row_id = await audit.record(
            device_id="thingwire-demo-001",
            action="read_temperature",
            params={},
            result={"value": 23.5, "unit": "celsius"},
        )
        assert row_id > 0

        entries = await audit.get_recent(device_id="thingwire-demo-001")
        assert len(entries) == 1
        assert entries[0]["action"] == "read_temperature"

    @pytest.mark.asyncio
    async def test_action_command_logged_with_confirmation(self, audit: AuditLog) -> None:
        """Dangerous actions are logged with confirmed=True."""
        await audit.record(
            device_id="thingwire-demo-001",
            action="do_set_relay",
            params={"state": True},
            result={"status": "command_sent", "action_id": "abc-123"},
            confirmed=True,
        )

        entries = await audit.get_recent(device_id="thingwire-demo-001")
        assert entries[0]["confirmed"] is True
        assert entries[0]["params"] == {"state": True}

    @pytest.mark.asyncio
    async def test_safety_rejection_logged(self, audit: AuditLog) -> None:
        """Safety rejections are recorded in audit log."""
        await audit.record(
            device_id="thingwire-demo-001",
            action="do_launchRocket",
            params={},
            result={"error": "ACTION_NOT_ALLOWED", "message": "Not in allowlist"},
        )

        entries = await audit.get_recent()
        assert entries[0]["result"]["error"] == "ACTION_NOT_ALLOWED"

    @pytest.mark.asyncio
    async def test_audit_log_query_by_device(self, audit: AuditLog) -> None:
        """Audit log can be filtered by device_id."""
        await audit.record("dev-1", "read_temp", {}, {"value": 20})
        await audit.record("dev-2", "read_temp", {}, {"value": 25})
        await audit.record("dev-1", "do_set_relay", {"state": True}, {"ok": True})

        dev1 = await audit.get_recent(device_id="dev-1")
        assert len(dev1) == 2

        dev2 = await audit.get_recent(device_id="dev-2")
        assert len(dev2) == 1


# ── End-to-End Wiring (no broker) ──


class TestEndToEndWiring:
    """Test the complete pipeline from raw MQTT data to tool output."""

    def test_device_to_reading(self, bridge: MqttBridge) -> None:
        """Raw MQTT data → bridge → reading dict with correct shape."""
        reading = bridge.get_latest_reading("thingwire-demo-001", "temp1")

        assert reading["device_id"] == "thingwire-demo-001"
        assert reading["property"] == "temp1"
        assert reading["value"] == 23.5
        assert reading["unit"] == "celsius"
        assert reading["timestamp"] == "2026-04-01T10:30:00Z"

    def test_all_properties_readable(self, bridge: MqttBridge) -> None:
        """All three sensor properties return valid readings."""
        temp = bridge.get_latest_reading("thingwire-demo-001", "temp1")
        assert temp["value"] == 23.5

        humidity = bridge.get_latest_reading("thingwire-demo-001", "humidity1")
        assert humidity["value"] == 45.2

        motion = bridge.get_latest_reading("thingwire-demo-001", "motion1")
        assert motion["value"] is True

    def test_missing_property_returns_error(self, bridge: MqttBridge) -> None:
        """Requesting a non-existent property returns error dict, not exception."""
        result = bridge.get_latest_reading("thingwire-demo-001", "pressure")
        assert "error" in result
        assert "available" in result

    def test_missing_device_returns_error(self, bridge: MqttBridge) -> None:
        """Requesting from unknown device returns error dict."""
        result = bridge.get_latest_reading("nonexistent", "temp1")
        assert "error" in result

    def test_device_status_reflects_injection(self, bridge: MqttBridge) -> None:
        """Injected online status is reflected."""
        assert bridge.get_device_status("thingwire-demo-001") == "online"
        assert bridge.is_device_online("thingwire-demo-001") is True

    @pytest.mark.asyncio
    async def test_full_read_with_audit(
        self, bridge: MqttBridge, audit: AuditLog
    ) -> None:
        """Complete read flow: bridge → reading → audit record."""
        reading = bridge.get_latest_reading("thingwire-demo-001", "temp1")
        await audit.record(
            device_id="thingwire-demo-001",
            action="read_temperature",
            params={},
            result=reading,
        )

        entries = await audit.get_recent(device_id="thingwire-demo-001")
        assert len(entries) == 1
        assert entries[0]["result"]["value"] == 23.5

    @pytest.mark.asyncio
    async def test_full_action_with_safety_and_audit(
        self, bridge: MqttBridge, safety: SafetyLayer, audit: AuditLog
    ) -> None:
        """Complete action flow: safety check → (simulated) command → audit."""
        # Safety checks pass
        safety.check_permission("thingwire-demo-001", "do_setRelay")
        safety.check_rate_limit("thingwire-demo-001", "do_setRelay")
        safety.record_heartbeat("thingwire-demo-001")
        safety.check_deadman_switch("thingwire-demo-001")

        # Confirmation for dangerous action
        assert safety.is_dangerous("thingwire-demo-001", "do_setRelay") is True
        msg = safety.require_confirmation("do_setRelay", {"state": True})
        assert "CONFIRMATION" in msg

        # Audit the action
        await audit.record(
            device_id="thingwire-demo-001",
            action="do_set_relay",
            params={"state": True},
            result={"status": "command_sent", "action_id": "test-123"},
            confirmed=True,
        )

        entries = await audit.get_recent(device_id="thingwire-demo-001")
        assert entries[0]["action"] == "do_set_relay"
        assert entries[0]["confirmed"] is True
