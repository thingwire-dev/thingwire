"""Per device rate limit across actions (contracts 5.2)."""

from typing import Any

from tests.test_safety_enum_slew import Clock, desk_td
from thingwire.safety import SafetyLayer


def make(global_cfg: dict[str, Any] | None = None) -> tuple[SafetyLayer, Clock]:
    clock = Clock()
    layer = SafetyLayer(clock=clock)
    if global_cfg:
        layer._config["global"].update(global_cfg)
    layer.register_device("desk-bot", desk_td())
    return layer, clock


def calls(layer: SafetyLayer, clock: Clock) -> list[str]:
    """Ten of each of three actions, 0.5 s apart; returns the verdict codes in order."""
    out: list[str] = []
    for action, params in (
        ("do_nod", {"times": 1}),
        ("do_set_expression", {"expression": "happy"}),
        ("do_look_at", {"pan": 10, "tilt": 0}),
    ):
        for _ in range(10):
            out.append(layer.evaluate("desk-bot", action, params).code)
            clock.now += 0.5
    return out


def test_rotating_actions_cannot_multiply_the_budget() -> None:
    layer, clock = make()
    codes = calls(layer, clock)
    assert codes.count("RATE_LIMIT_EXCEEDED") == 20
    assert codes[:10] == ["OK"] * 10


def test_denial_reports_the_device_scope() -> None:
    layer, clock = make()
    calls(layer, clock)
    verdict = layer.evaluate("desk-bot", "do_nod", {"times": 1})
    assert verdict.code == "RATE_LIMIT_EXCEEDED"
    assert verdict.details["scope"] == "device"


def test_explicit_device_limit_overrides_the_default() -> None:
    layer, clock = make({"device_rate_limit": {"max_calls": 25, "window_seconds": 60}})
    assert calls(layer, clock).count("RATE_LIMIT_EXCEEDED") == 5


def test_per_device_override_beats_global() -> None:
    layer, clock = make({"device_rate_limit": {"max_calls": 25, "window_seconds": 60}})
    layer._config["devices"]["desk-bot"]["device_rate_limit"] = {
        "max_calls": 12,
        "window_seconds": 60,
    }
    assert calls(layer, clock).count("RATE_LIMIT_EXCEEDED") == 18


def test_per_action_limit_still_applies_below_the_device_limit() -> None:
    layer, _ = make({"device_rate_limit": {"max_calls": 100, "window_seconds": 60}})
    layer._config["devices"]["desk-bot"]["rate_limits"]["do_nod"] = {
        "max_calls": 2,
        "window_seconds": 60,
    }
    codes = [layer.evaluate("desk-bot", "do_nod", {"times": 1}).code for _ in range(4)]
    assert codes == ["OK", "OK", "RATE_LIMIT_EXCEEDED", "RATE_LIMIT_EXCEEDED"]
    assert layer.evaluate("desk-bot", "do_nod", {"times": 1}).details["scope"] == "action"


def test_a_call_denied_by_one_window_is_recorded_in_neither() -> None:
    layer, _ = make({"device_rate_limit": {"max_calls": 100, "window_seconds": 60}})
    layer._config["devices"]["desk-bot"]["rate_limits"]["do_nod"] = {
        "max_calls": 1,
        "window_seconds": 60,
    }
    layer.evaluate("desk-bot", "do_nod", {"times": 1})
    for _ in range(5):
        layer.evaluate("desk-bot", "do_nod", {"times": 1})  # denied by the action window
    assert len(layer._rates.device_limiters["desk-bot"].timestamps) == 1


def test_window_recovery_and_prune() -> None:
    layer, clock = make()
    calls(layer, clock)
    clock.now += 120
    assert layer.evaluate("desk-bot", "do_nod", {"times": 1}).allowed
    clock.now += 120
    layer.prune_rate_limiters()
    assert layer._rates.device_limiters == {}


def test_malformed_device_limit_fails_closed() -> None:
    layer, _ = make({"device_rate_limit": {"max_calls": "lots", "window_seconds": 60}})
    verdict = layer.evaluate("desk-bot", "do_nod", {"times": 1})
    assert (verdict.allowed, verdict.code) == (False, "SAFETY_CHECK_ERROR")
