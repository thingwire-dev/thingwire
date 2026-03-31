"""Tests for hardened behavior — edge cases, state management, resilience."""

import time

import pytest

from thingwire.config import GatewayConfig
from thingwire.mqtt_bridge import ConnectionState, MqttBridge
from thingwire.safety import SafetyError, SafetyLayer


# ── Connection State Machine ──


class TestConnectionState:
    """Test MQTT bridge connection state tracking."""

    def test_initial_state_is_disconnected(self) -> None:
        config = GatewayConfig(mqtt_broker="localhost")
        bridge = MqttBridge(config)
        assert bridge.state == ConnectionState.DISCONNECTED

    def test_state_enum_values(self) -> None:
        assert ConnectionState.DISCONNECTED.value == "disconnected"
        assert ConnectionState.CONNECTING.value == "connecting"
        assert ConnectionState.CONNECTED.value == "connected"
        assert ConnectionState.RECONNECTING.value == "reconnecting"


# ── Telemetry-Based Heartbeat ──


class TestTelemetryHeartbeat:
    """Test that telemetry messages update heartbeat timestamps."""

    def test_telemetry_updates_heartbeat_time(self) -> None:
        """Receiving telemetry should record a monotonic timestamp."""
        config = GatewayConfig(mqtt_broker="localhost")
        bridge = MqttBridge(config)

        import json

        telemetry = {
            "timestamp": "2026-04-01T10:00:00Z",
            "readings": {"temp1": {"value": 22.0, "unit": "celsius"}},
        }
        bridge._handle_telemetry("dev-1", json.dumps(telemetry).encode())

        ts = bridge.get_last_telemetry_time("dev-1")
        assert ts is not None
        assert ts > 0

    def test_no_telemetry_returns_none(self) -> None:
        """Device with no telemetry should return None for last time."""
        config = GatewayConfig(mqtt_broker="localhost")
        bridge = MqttBridge(config)
        assert bridge.get_last_telemetry_time("unknown") is None

    def test_safety_uses_telemetry_heartbeat(self) -> None:
        """Safety layer should accept telemetry timestamps as heartbeats."""
        safety = SafetyLayer()
        safety.register_device("dev-1", {"properties": {}, "actions": {}})

        # Simulate a recent telemetry timestamp
        recent_time = time.monotonic()
        safety.update_heartbeat_from_telemetry("dev-1", recent_time)
        safety.check_deadman_switch("dev-1")  # Should not raise

    def test_stale_telemetry_triggers_deadman(self) -> None:
        """Old telemetry timestamp should trigger deadman switch."""
        safety = SafetyLayer()
        safety.register_device("dev-1", {"properties": {}, "actions": {}})

        old_time = time.monotonic() - 600  # 10 minutes ago
        safety.update_heartbeat_from_telemetry("dev-1", old_time)

        with pytest.raises(SafetyError, match="has not sent a heartbeat"):
            safety.check_deadman_switch("dev-1")

    def test_none_telemetry_time_is_noop(self) -> None:
        """Passing None telemetry time should not update heartbeat."""
        safety = SafetyLayer()
        safety.update_heartbeat_from_telemetry("dev-1", None)
        # No heartbeat recorded, so deadman check should pass (first command allowed)
        safety.check_deadman_switch("dev-1")


# ── Rate Limiter Pruning ──


class TestRateLimiterPruning:
    """Test rate limiter memory management."""

    def test_prune_removes_expired_windows(self) -> None:
        """Pruning should remove rate limiters with no recent calls."""
        safety = SafetyLayer()
        safety.register_device("dev-1", {
            "properties": {},
            "actions": {"setRelay": {"safe": False}},
        })

        # Make some calls
        safety.check_rate_limit("dev-1", "do_setRelay")
        safety.check_rate_limit("dev-1", "do_setRelay")

        # Manually expire the timestamps
        for action_limiters in safety._rate_limiters.values():
            for limiter in action_limiters.values():
                limiter.timestamps = [time.monotonic() - 120]  # 2 min ago, window is 60s

        pruned = safety.prune_rate_limiters()
        assert pruned >= 1
        # After pruning, the limiter should be gone
        assert len(safety._rate_limiters) == 0

    def test_prune_keeps_active_windows(self) -> None:
        """Pruning should keep rate limiters with recent calls."""
        safety = SafetyLayer()
        safety.register_device("dev-1", {
            "properties": {},
            "actions": {"setRelay": {"safe": False}},
        })

        safety.check_rate_limit("dev-1", "do_setRelay")

        pruned = safety.prune_rate_limiters()
        assert pruned == 0  # Recent call, should not be pruned


# ── Send Command Validation ──


class TestSendCommandValidation:
    """Test command sending edge cases."""

    def test_send_without_connection_raises(self) -> None:
        """Sending a command when not connected should raise."""
        config = GatewayConfig(mqtt_broker="localhost")
        bridge = MqttBridge(config)

        import asyncio

        with pytest.raises(RuntimeError, match="Not connected"):
            asyncio.get_event_loop().run_until_complete(
                bridge.send_command("dev-1", "relay1", "set", True)
            )


# ── Graceful Shutdown ──


class TestGracefulShutdown:
    """Test clean shutdown behavior."""

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected(self) -> None:
        """Disconnecting when not connected should be a no-op."""
        config = GatewayConfig(mqtt_broker="localhost")
        bridge = MqttBridge(config)
        await bridge.disconnect()  # Should not raise
        assert bridge.state == ConnectionState.DISCONNECTED
