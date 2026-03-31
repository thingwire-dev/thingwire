"""Tests for the safety layer."""

import time

import pytest

from thingwire.safety import SafetyError, SafetyLayer


@pytest.fixture
def safety() -> SafetyLayer:
    """Create a safety layer with no config file (uses defaults)."""
    layer = SafetyLayer()
    layer.register_device(
        "test-001",
        {
            "properties": {"temperature": {}, "humidity": {}},
            "actions": {
                "setRelay": {"safe": False},
                "readSensor": {"safe": True},
            },
        },
    )
    return layer


def test_allowed_action_passes(safety: SafetyLayer) -> None:
    """Allowed actions should pass without error."""
    safety.check_permission("test-001", "read_temperature")
    safety.check_permission("test-001", "do_setRelay")


def test_unauthorized_action_rejected(safety: SafetyLayer) -> None:
    """Actions not in allowlist should be rejected."""
    with pytest.raises(SafetyError, match="not in the allowlist"):
        safety.check_permission("test-001", "do_launchMissile")


def test_unregistered_device_rejected(safety: SafetyLayer) -> None:
    """Commands to unknown devices should be rejected."""
    with pytest.raises(SafetyError, match="not registered"):
        safety.check_permission("unknown-device", "read_temperature")


def test_unauthorized_error_code(safety: SafetyLayer) -> None:
    """SafetyError should carry the correct error code."""
    with pytest.raises(SafetyError) as exc_info:
        safety.check_permission("test-001", "do_launchMissile")
    assert exc_info.value.code == "ACTION_NOT_ALLOWED"


def test_rate_limit_enforced(safety: SafetyLayer) -> None:
    """Rate limit should reject after max_calls."""
    for _ in range(10):
        safety.check_rate_limit("test-001", "do_setRelay")

    with pytest.raises(SafetyError, match="Rate limit exceeded"):
        safety.check_rate_limit("test-001", "do_setRelay")


def test_dangerous_action_detected(safety: SafetyLayer) -> None:
    """Actions marked safe=false should be detected as dangerous."""
    assert safety.is_dangerous("test-001", "do_setRelay") is True
    assert safety.is_dangerous("test-001", "read_temperature") is False


def test_confirmation_message(safety: SafetyLayer) -> None:
    """Confirmation should return a meaningful message."""
    msg = safety.require_confirmation("do_setRelay", {"state": True})
    assert "CONFIRMATION REQUIRED" in msg
    assert "do_setRelay" in msg


def test_deadman_switch(safety: SafetyLayer) -> None:
    """Deadman switch should block commands after heartbeat timeout."""
    safety.record_heartbeat("test-001")
    safety._last_heartbeat["test-001"] = time.monotonic() - 600

    with pytest.raises(SafetyError, match="has not sent a heartbeat"):
        safety.check_deadman_switch("test-001")


def test_deadman_switch_passes_with_recent_heartbeat(safety: SafetyLayer) -> None:
    """Deadman switch should pass with a recent heartbeat."""
    safety.record_heartbeat("test-001")
    safety.check_deadman_switch("test-001")  # Should not raise
