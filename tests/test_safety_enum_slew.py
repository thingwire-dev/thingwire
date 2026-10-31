"""Enum enforcement and property to axis slew pairing (contracts 2.1 and 5.1)."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from thingwire.safety import SafetyLayer
from thingwire.safety_motion import extract_limits

LIB = Path(__file__).resolve().parents[1] / "td-library" / "desk-bot.td.json"


def desk_td() -> dict[str, Any]:
    text = LIB.read_text().replace("{device_id}", "desk-bot").replace("{broker}", "b")
    td: dict[str, Any] = json.loads(text)
    return td


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def layer_for(td: dict[str, Any], mode: str = "reject") -> tuple[SafetyLayer, Clock]:
    clock = Clock()
    layer = SafetyLayer(clock=clock)
    layer._config["global"]["out_of_range"] = mode
    layer._motion.mode = mode
    layer.register_device("desk-bot", td)
    return layer, clock


@pytest.mark.parametrize(
    ("value", "allowed"),
    [
        ("happy", True),
        ("alert", True),
        ("<script>pwn</script>", False),
        ("", False),
        ("HAPPY", False),
        (1, False),
        (True, False),
        (None, False),
        (["happy"], False),
    ],
)
def test_string_enum_is_enforced(value: Any, allowed: bool) -> None:
    layer, _ = layer_for(desk_td())
    verdict = layer.evaluate("desk-bot", "do_set_expression", {"expression": value})
    assert verdict.allowed is allowed
    if not allowed:
        assert verdict.code == "INVALID_INPUT"
        assert verdict.details["reason"] == "not_in_enum"
        assert verdict.details["axis"] == "expression"


def test_enum_is_never_clamped() -> None:
    layer, _ = layer_for(desk_td(), mode="clamp")
    verdict = layer.evaluate("desk-bot", "do_set_expression", {"expression": "bogus"})
    assert (verdict.allowed, verdict.code) == (False, "INVALID_INPUT")


def test_omitted_enum_input_is_not_checked() -> None:
    layer, _ = layer_for(desk_td())
    assert layer.evaluate("desk-bot", "do_set_expression", {}).allowed


def test_numeric_enum_compares_by_json_type() -> None:
    td = {
        "actions": {
            "mode": {
                "safe": True,
                "input": {
                    "type": "object",
                    "properties": {"m": {"type": "integer", "enum": [1, 2]}},
                },
            }
        }
    }
    layer, _ = layer_for(td)
    assert layer.evaluate("desk-bot", "do_mode", {"m": 2}).allowed
    assert layer.evaluate("desk-bot", "do_mode", {"m": 2.0}).allowed
    assert not layer.evaluate("desk-bot", "do_mode", {"m": True}).allowed
    assert not layer.evaluate("desk-bot", "do_mode", {"m": "1"}).allowed
    assert not layer.evaluate("desk-bot", "do_mode", {"m": 3}).allowed


def test_look_at_slew_comes_from_the_properties() -> None:
    limits = extract_limits(desk_td()["actions"]["look_at"], desk_td()["properties"])
    assert limits["pan"].max_slew_deg_per_sec == 90
    assert limits["tilt"].max_slew_deg_per_sec == 90
    assert limits["pan"].slew_key == "head_pan"


def test_look_at_swing_faster_than_slew_is_refused() -> None:
    layer, clock = layer_for(desk_td())
    assert layer.evaluate("desk-bot", "do_look_at", {"pan": -80, "tilt": 0}).allowed
    clock.now += 0.5
    verdict = layer.evaluate("desk-bot", "do_look_at", {"pan": 80, "tilt": 0})
    assert (verdict.allowed, verdict.code) == (False, "SLEW_EXCEEDED")
    assert verdict.details["axis"] == "pan"


def test_look_at_slew_allows_a_slow_enough_move() -> None:
    layer, clock = layer_for(desk_td())
    assert layer.evaluate("desk-bot", "do_look_at", {"pan": -80, "tilt": 0}).allowed
    clock.now += 2.0  # 160 degrees at 90 deg/s needs 1.78 s
    assert layer.evaluate("desk-bot", "do_look_at", {"pan": 80, "tilt": 0}).allowed


def test_denied_move_does_not_reset_the_slew_history() -> None:
    layer, clock = layer_for(desk_td())
    assert layer.evaluate("desk-bot", "do_look_at", {"pan": 0, "tilt": 0}).allowed
    clock.now += 0.1
    assert not layer.evaluate("desk-bot", "do_look_at", {"pan": 80, "tilt": 0}).allowed
    clock.now += 0.1  # only 0.2 s since the last accepted command
    assert not layer.evaluate("desk-bot", "do_look_at", {"pan": 40, "tilt": 0}).allowed


def test_tilt_axis_is_paired_with_head_tilt() -> None:
    layer, clock = layer_for(desk_td())
    assert layer.evaluate("desk-bot", "do_look_at", {"pan": 0, "tilt": -30}).allowed
    clock.now += 0.2
    verdict = layer.evaluate("desk-bot", "do_look_at", {"pan": 0, "tilt": 30})
    assert (verdict.code, verdict.details["axis"]) == ("SLEW_EXCEEDED", "tilt")


def test_smaller_of_axis_and_property_slew_wins() -> None:
    td = desk_td()
    td["actions"]["look_at"]["input"]["properties"]["pan"]["maxSlewDegPerSec"] = 20
    assert (
        extract_limits(td["actions"]["look_at"], td["properties"])["pan"].max_slew_deg_per_sec == 20
    )
    td["actions"]["look_at"]["input"]["properties"]["pan"]["maxSlewDegPerSec"] = 500
    assert (
        extract_limits(td["actions"]["look_at"], td["properties"])["pan"].max_slew_deg_per_sec == 90
    )


def test_exact_name_beats_suffix_and_ambiguous_suffix_takes_the_strictest() -> None:
    props = {
        "pan": {"type": "number", "maxSlewDegPerSec": 50},
        "head_pan": {"type": "number", "maxSlewDegPerSec": 10},
        "neck_tilt": {"type": "number", "maxSlewDegPerSec": 30},
        "arm_tilt": {"type": "number", "maxSlewDegPerSec": 15},
    }
    action = {
        "input": {
            "type": "object",
            "properties": {
                "pan": {"type": "number", "minimum": -1, "maximum": 1},
                "tilt": {"type": "number", "minimum": -1, "maximum": 1},
            },
        }
    }
    limits = extract_limits(action, props)
    assert limits["pan"].max_slew_deg_per_sec == 50  # exact match only
    assert limits["tilt"].max_slew_deg_per_sec == 15  # strictest of two suffix matches


def test_non_numeric_or_unrelated_property_gives_no_slew() -> None:
    props = {"expression": {"type": "string", "maxSlewDegPerSec": 1}, "other": {"type": "number"}}
    action = {
        "input": {
            "type": "object",
            "properties": {"pan": {"type": "number", "minimum": -1, "maximum": 1}},
        }
    }
    assert extract_limits(action, props)["pan"].max_slew_deg_per_sec is None


def test_two_actions_driving_one_property_share_slew_history() -> None:
    td = desk_td()
    other = copy.deepcopy(td["actions"]["look_at"])
    td["actions"]["glance"] = other
    layer, clock = layer_for(td)
    assert layer.evaluate("desk-bot", "do_look_at", {"pan": -80, "tilt": 0}).allowed
    clock.now += 0.5
    verdict = layer.evaluate("desk-bot", "do_glance", {"pan": 80, "tilt": 0})
    assert (verdict.allowed, verdict.code) == (False, "SLEW_EXCEEDED")


def test_extract_limits_without_properties_is_unchanged() -> None:
    limits = extract_limits(desk_td()["actions"]["look_at"])
    assert limits["pan"].max_slew_deg_per_sec is None
