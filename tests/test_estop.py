"""Table-driven tests for the latched e-stop, persistence, deadman and flood throttle."""

from pathlib import Path
from typing import Any

import pytest

from thingwire.safety import SafetyError

from .estop_helpers import make_layer

OK_CMD = {"angle": 10}


@pytest.mark.parametrize(
    ("scope", "device", "action", "params", "allowed", "code"),
    [
        ("servo", "servo", "do_setAngle", OK_CMD, False, "ESTOP_LATCHED"),
        ("servo", "servo", "read_angle", {}, True, "OK"),
        (None, "servo", "do_setAngle", OK_CMD, False, "ESTOP_LATCHED"),
        (None, "other", "do_setAngle", OK_CMD, False, "ESTOP_LATCHED"),
        (None, "servo", "read_angle", {}, True, "OK"),
        ("servo", "other", "do_setAngle", OK_CMD, True, "OK"),
    ],
)
def test_latch_denies_everything_but_reads(
    tmp_path: Path,
    scope: str | None,
    device: str,
    action: str,
    params: dict[str, Any],
    allowed: bool,
    code: str,
) -> None:
    layer, _, _ = make_layer(tmp_path)
    layer.register_device("other", {"actions": {"setAngle": {"safe": True}}})
    assert layer.trip_estop(scope, "test", "api")
    assert not layer.trip_estop(scope, "again", "api")  # idempotent
    verdict = layer.evaluate(device, action, params)
    assert (verdict.allowed, verdict.code) == (allowed, code)
    assert verdict.details["trace_id"]


@pytest.mark.parametrize(
    ("who", "reason", "ok"),
    [
        ("operator", "cleared by hand", True),
        ("intruder", "cleared by hand", False),
        ("", "cleared by hand", False),
        ("operator", "  ", False),
    ],
)
def test_reset_requires_authorized_who_and_reason(
    tmp_path: Path, who: str, reason: str, ok: bool
) -> None:
    layer, _, _ = make_layer(tmp_path)
    layer.trip_estop("servo", "test")
    if ok:
        assert layer.reset_estop("servo", reason, who)
        assert layer.evaluate("servo", "do_setAngle", OK_CMD).allowed
    else:
        with pytest.raises(SafetyError) as exc:
            layer.reset_estop("servo", reason, who)
        assert exc.value.code == "RESET_NOT_AUTHORIZED"
        assert layer.estop_latched("servo")


def test_reset_refused_when_no_resetters_configured(tmp_path: Path) -> None:
    layer, _, _ = make_layer(tmp_path, resetters=[])
    layer.trip_estop("servo", "test")
    with pytest.raises(SafetyError):
        layer.reset_estop("servo", "why", "operator")


def test_device_reset_does_not_clear_global(tmp_path: Path) -> None:
    layer, _, _ = make_layer(tmp_path)
    layer.trip_estop(None, "global")
    layer.trip_estop("servo", "device")
    layer.reset_estop("servo", "r", "operator")
    assert layer.evaluate("servo", "do_setAngle", OK_CMD).code == "ESTOP_LATCHED"
    layer.reset_estop(None, "r", "operator")
    assert layer.evaluate("servo", "do_setAngle", OK_CMD).allowed


@pytest.mark.parametrize("scope", ["servo", None])
def test_latch_survives_restart_and_reset_persists(tmp_path: Path, scope: str | None) -> None:
    layer, clock, _ = make_layer(tmp_path)
    layer.trip_estop(scope, "persist me")
    again, _, events = make_layer(tmp_path, clock=clock)  # same db, fresh process
    assert again.evaluate("servo", "do_setAngle", OK_CMD).code == "ESTOP_LATCHED"
    assert [e.state.value for e in events] == ["estop"]  # published on registration
    again.reset_estop(scope, "done", "operator")
    third, _, _ = make_layer(tmp_path, clock=clock)
    assert third.evaluate("servo", "do_setAngle", OK_CMD).allowed


def test_unreadable_state_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "state.db").write_bytes(b"this is not sqlite" * 50)
    layer, _, _ = make_layer(tmp_path)
    assert layer.evaluate("servo", "do_setAngle", OK_CMD).code == "ESTOP_LATCHED"
    assert layer.evaluate("servo", "read_angle", {}).allowed


def test_migration_is_idempotent(tmp_path: Path) -> None:
    for _ in range(3):
        make_layer(tmp_path)


def test_deadman_expiry_latches_estop(tmp_path: Path) -> None:
    layer, clock, events = make_layer(tmp_path)
    layer.record_heartbeat("servo")
    clock.now += 50
    assert layer.evaluate("servo", "do_setAngle", OK_CMD).allowed
    clock.now += 500
    assert layer.evaluate("servo", "do_setAngle", OK_CMD).code == "DEADMAN_SWITCH"
    layer.record_heartbeat("servo")  # device is back, but the latch holds
    assert layer.evaluate("servo", "do_setAngle", OK_CMD).code == "ESTOP_LATCHED"
    tripped = [e for e in events if e.event == "estop_tripped"]
    assert [(e.actor, e.last_decision_code) for e in tripped] == [("deadman", "DEADMAN_SWITCH")]
    layer.reset_estop("servo", "inspected", "operator")
    assert layer.evaluate("servo", "do_setAngle", OK_CMD).allowed


def test_sweep_deadman_trips_without_a_command(tmp_path: Path) -> None:
    layer, clock, _ = make_layer(tmp_path)
    layer.record_heartbeat("servo")
    clock.now += 500
    layer.sweep_deadman()
    assert layer.estop_latched("servo")


FLOOD_CASES = [
    ({"angle": 999}, "OUT_OF_RANGE"),
    ({"angle": "x"}, "INVALID_INPUT"),
]


@pytest.mark.parametrize(("bad", "code"), FLOOD_CASES)
def test_denial_flood_throttles_then_recovers(
    tmp_path: Path, bad: dict[str, Any], code: str
) -> None:
    layer, clock, events = make_layer(tmp_path, threshold=3)
    assert [layer.evaluate("servo", "do_setAngle", bad).code for _ in range(5)] == [
        code,
        code,
        code,
        "DENIAL_FLOOD",
        "DENIAL_FLOOD",
    ]
    assert layer.evaluate("servo", "do_setAngle", OK_CMD).code == "DENIAL_FLOOD"
    degraded = [e for e in events if e.state.value == "degraded"]
    assert len(degraded) == 1 and degraded[0].drop_reason == "DENIAL_FLOOD"
    clock.now += 11
    layer.guards.tick()
    assert events[-1].state.value == "ok"
    assert layer.evaluate("servo", "do_setAngle", OK_CMD).allowed


def test_flood_is_per_action_and_device(tmp_path: Path) -> None:
    layer, _, _ = make_layer(tmp_path, threshold=2)
    for _ in range(2):
        layer.evaluate("servo", "do_setAngle", {"angle": 999})
    assert layer.evaluate("servo", "read_angle", {}).allowed
    assert layer.evaluate("servo", "do_setAngle", {"angle": 999}).code == "DENIAL_FLOOD"


def test_flood_disabled_when_threshold_zero(tmp_path: Path) -> None:
    layer, _, _ = make_layer(tmp_path, threshold=0)
    codes = {layer.evaluate("servo", "do_setAngle", {"angle": 999}).code for _ in range(30)}
    assert codes == {"OUT_OF_RANGE"}
