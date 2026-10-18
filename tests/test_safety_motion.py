"""Table-driven tests for motion safety: ranges, clamp, slew, rate limit, approval."""

from typing import Any

import pytest

from thingwire.safety import SafetyLayer

SERVO_TD: dict[str, Any] = {
    "actions": {
        "setAngle": {
            "safe": True,
            "input": {
                "type": "object",
                "properties": {
                    "angle": {"type": "number", "minimum": 0, "maximum": 180, "unit": "deg"}
                },
            },
        },
        "fastPan": {
            "safe": True,
            "input": {
                "type": "object",
                "properties": {
                    "pan": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 360,
                        "maxSlewDegPerSec": 10,
                    }
                },
            },
        },
        "sweep": {"safe": False, "input": {"type": "object", "properties": {}}},
    }
}


class FakeClock:
    """Manually advanced clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make(mode: str = "reject") -> tuple[SafetyLayer, FakeClock]:
    clock = FakeClock()
    layer = SafetyLayer(clock=clock)
    layer._config["global"]["out_of_range"] = mode
    layer._motion.mode = mode
    layer.register_device("servo", SERVO_TD)
    return layer, clock


RANGE_CASES = [
    ("reject", 90, True, "OK", 90),
    ("reject", 0, True, "OK", 0),
    ("reject", 180, True, "OK", 180),
    ("reject", 181, False, "OUT_OF_RANGE", None),
    ("reject", -1, False, "OUT_OF_RANGE", None),
    ("reject", float("nan"), False, "INVALID_INPUT", None),
    ("reject", "90", False, "INVALID_INPUT", None),
    ("reject", True, False, "INVALID_INPUT", None),
    ("clamp", 200, True, "RANGE_CLAMPED", 180),
    ("clamp", -50, True, "RANGE_CLAMPED", 0),
    ("clamp", 45, True, "OK", 45),
]


@pytest.mark.parametrize(("mode", "value", "allowed", "code", "applied"), RANGE_CASES)
def test_range_handling(
    mode: str, value: Any, allowed: bool, code: str, applied: float | None
) -> None:
    layer, _ = make(mode)
    result = layer.evaluate("servo", "do_setAngle", {"angle": value})
    assert result.allowed is allowed
    assert result.code == code
    assert result.message
    if applied is not None:
        assert result.params["angle"] == applied


def test_clamp_details_report_requested_and_applied() -> None:
    layer, _ = make("clamp")
    result = layer.evaluate("servo", "do_setAngle", {"angle": 999})
    assert result.details["angle"] == {"requested": 999.0, "applied": 180.0}


# (first value, seconds later, second value, allowed, code)
SLEW_CASES = [
    (0, 1.0, 90, True, "OK"),
    (0, 1.0, 91, False, "SLEW_EXCEEDED"),
    (0, 2.0, 180, True, "OK"),
    (0, 0.0, 10, False, "SLEW_EXCEEDED"),
    (100, 0.5, 100, True, "OK"),
]


@pytest.mark.parametrize(("first", "dt", "second", "allowed", "code"), SLEW_CASES)
def test_default_slew_90_deg_per_sec(
    first: float, dt: float, second: float, allowed: bool, code: str
) -> None:
    layer, clock = make()
    assert layer.evaluate("servo", "do_setAngle", {"angle": first}).allowed
    clock.now += dt
    result = layer.evaluate("servo", "do_setAngle", {"angle": second})
    assert (result.allowed, result.code) == (allowed, code)


def test_per_axis_slew_override_and_denied_move_keeps_state() -> None:
    layer, clock = make()
    layer.evaluate("servo", "do_fastPan", {"pan": 0})
    clock.now += 1
    assert layer.evaluate("servo", "do_fastPan", {"pan": 11}).code == "SLEW_EXCEEDED"
    assert layer.evaluate("servo", "do_fastPan", {"pan": 10}).allowed


UNITS_TD: dict[str, Any] = {
    "actions": {
        "setLevel": {
            "safe": True,
            "input": {
                "type": "object",
                "properties": {"level": {"type": "number", "minimum": 0, "maximum": 255}},
            },
        },
        "setDeg": {
            "safe": True,
            "input": {
                "type": "object",
                "properties": {
                    "angle": {"type": "number", "minimum": 0, "maximum": 180, "unit": "deg"}
                },
            },
        },
        "setLimited": {
            "safe": True,
            "input": {
                "type": "object",
                "properties": {
                    "level": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 255,
                        "maxSlewDegPerSec": 10,
                    }
                },
            },
        },
    }
}


def make_units_layer() -> tuple[SafetyLayer, FakeClock]:
    clock = FakeClock()
    layer = SafetyLayer(clock=clock)
    layer.register_device("dev", UNITS_TD)
    return layer, clock


def test_unitless_axis_gets_no_default_slew() -> None:
    layer, _ = make_units_layer()
    assert layer.evaluate("dev", "do_setLevel", {"level": 0}).allowed
    result = layer.evaluate("dev", "do_setLevel", {"level": 255})
    assert (result.allowed, result.code) == (True, "OK")


@pytest.mark.parametrize(
    ("dt", "second", "allowed", "code"),
    [(1.0, 90, True, "OK"), (1.0, 91, False, "SLEW_EXCEEDED")],
)
def test_deg_unit_axis_gets_default_90_deg_per_sec(
    dt: float, second: float, allowed: bool, code: str
) -> None:
    layer, clock = make_units_layer()
    assert layer.evaluate("dev", "do_setDeg", {"angle": 0}).allowed
    clock.now += dt
    result = layer.evaluate("dev", "do_setDeg", {"angle": second})
    assert (result.allowed, result.code) == (allowed, code)


def test_unitless_axis_with_explicit_slew_is_limited() -> None:
    layer, clock = make_units_layer()
    assert layer.evaluate("dev", "do_setLimited", {"level": 0}).allowed
    clock.now += 1
    assert layer.evaluate("dev", "do_setLimited", {"level": 11}).code == "SLEW_EXCEEDED"
    assert layer.evaluate("dev", "do_setLimited", {"level": 10}).allowed


def test_rate_limit_hit_and_window_recovery() -> None:
    layer, clock = make()
    layer._config["devices"]["servo"]["rate_limits"]["do_setAngle"] = {
        "max_calls": 2,
        "window_seconds": 10,
    }
    assert layer.evaluate("servo", "do_setAngle", {"angle": 1}).allowed
    clock.now += 1
    assert layer.evaluate("servo", "do_setAngle", {"angle": 2}).allowed
    clock.now += 1
    blocked = layer.evaluate("servo", "do_setAngle", {"angle": 3})
    assert (blocked.allowed, blocked.code) == (False, "RATE_LIMIT_EXCEEDED")
    clock.now += 20
    assert layer.evaluate("servo", "do_setAngle", {"angle": 3}).allowed


@pytest.mark.parametrize(
    ("approved", "allowed", "code"),
    [(False, False, "APPROVAL_REQUIRED"), (True, True, "OK")],
)
def test_dangerous_action_requires_approval(approved: bool, allowed: bool, code: str) -> None:
    layer, _ = make()
    result = layer.evaluate("servo", "do_sweep", {}, approved=approved)
    assert (result.allowed, result.code) == (allowed, code)


@pytest.mark.parametrize(
    ("device", "action", "code"),
    [("ghost", "do_setAngle", "DEVICE_NOT_REGISTERED"), ("servo", "do_nuke", "ACTION_NOT_ALLOWED")],
)
def test_permission_denials_are_structured(device: str, action: str, code: str) -> None:
    layer, _ = make()
    result = layer.evaluate(device, action, {})
    assert (result.allowed, result.code) == (False, code)


def test_exception_inside_check_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    layer, _ = make()

    def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("sensor bus exploded")

    monkeypatch.setattr(layer._motion, "check", boom)
    result = layer.evaluate("servo", "do_setAngle", {"angle": 10})
    assert result.allowed is False
    assert result.code == "SAFETY_CHECK_ERROR"
    assert "RuntimeError" in result.details["error"]


def test_identity_args_accepted() -> None:
    layer, _ = make()
    result = layer.evaluate(
        "servo", "do_setAngle", {"angle": 5}, agent_id="a1", delegation_chain=["u"], scopes=["x"]
    )
    assert result.allowed
