"""show_text and blink input rules, gateway side (contracts 5.6, test pins in section 9)."""

import json
from pathlib import Path
from typing import Any

import pytest

from thingwire.safety import SafetyLayer
from thingwire.safety_motion import extract_limits
from thingwire.safety_pins import PinnedAxis, merge_axis

TD = json.loads(
    (Path(__file__).resolve().parent.parent / "td-library" / "desk-bot.td.json").read_text()
)
MAX_CHARS = TD["actions"]["show_text"]["input"]["properties"]["text"]["maxLength"]
MAX_MS = TD["actions"]["show_text"]["input"]["properties"]["duration_ms"]["maximum"]
MAX_TIMES = TD["actions"]["blink"]["input"]["properties"]["times"]["maximum"]


class Clock:
    now = 1000.0

    def __call__(self) -> float:
        return self.now


def make(mode: str = "reject") -> SafetyLayer:
    layer = SafetyLayer(clock=Clock())
    layer._config["global"]["out_of_range"] = mode
    layer._motion.mode = mode
    layer.register_device("bot", TD)
    return layer


def run(layer: SafetyLayer, action: str, params: dict[str, Any]) -> Any:
    return layer.evaluate("bot", action, params)


TEXT_CASES: list[tuple[Any, str]] = [
    ("x" * MAX_CHARS, "OK"),
    (" ~", "OK"),
    ("x" * (MAX_CHARS + 1), "too_long"),
    ("", "blank"),
    ("   ", "blank"),
    ("a\nb", "control_char"),
    ("a\x00b", "control_char"),
    ("a\x1fb", "control_char"),
    ("a\x7fb", "control_char"),
    ("café", "charset"),
    ("hi \U0001f600", "charset"),
    (42, "not_string"),
    (None, "not_string"),
]


@pytest.mark.parametrize(("text", "outcome"), TEXT_CASES)
def test_text_charset_and_length(text: Any, outcome: str) -> None:
    verdict = run(make(), "do_show_text", {"text": text})
    if outcome == "OK":
        assert verdict.allowed and verdict.code == "OK"
        return
    assert not verdict.allowed and verdict.code == "INVALID_INPUT"
    assert verdict.details["reason"] == outcome
    assert "text" not in {k for k in verdict.details if k != "axis"}
    assert str(text) not in json.dumps(verdict.details) or str(text) in ("", "42", "None")


def test_text_is_never_clamped_or_rewritten() -> None:
    layer = make("clamp")
    long = "y" * (MAX_CHARS + 5)
    assert run(layer, "do_show_text", {"text": long}).code == "INVALID_INPUT"
    ok = run(layer, "do_show_text", {"text": "hello"})
    assert ok.params["text"] == "hello"


def test_missing_text_and_unknown_key() -> None:
    layer = make()
    assert run(layer, "do_show_text", {}).details["reason"] == "missing"
    unknown = run(layer, "do_show_text", {"text": "hi", "color": "red"})
    assert unknown.code == "INVALID_INPUT" and unknown.details["reason"] == "unknown_property"
    assert run(layer, "do_blink", {"times": 1, "x": 1}).details["reason"] == "unknown_property"


@pytest.mark.parametrize("mode", ["reject", "clamp"])
@pytest.mark.parametrize(
    ("action", "axis", "bad"),
    [
        ("do_show_text", "duration_ms", 0),
        ("do_show_text", "duration_ms", MAX_MS + 1),
        ("do_blink", "times", 0),
        ("do_blink", "times", MAX_TIMES + 1),
    ],
)
def test_integer_bounds_are_never_clamped(mode: str, action: str, axis: str, bad: int) -> None:
    params = {"text": "hi", axis: bad} if action == "do_show_text" else {axis: bad}
    verdict = run(make(mode), action, params)
    assert not verdict.allowed and verdict.code == "OUT_OF_RANGE"


@pytest.mark.parametrize(
    ("action", "params"),
    [
        ("do_show_text", {"text": "hi", "duration_ms": MAX_MS}),
        ("do_show_text", {"text": "hi", "duration_ms": 1}),
        ("do_blink", {"times": 1}),
        ("do_blink", {"times": MAX_TIMES, "speed": "slow"}),
    ],
)
def test_bounds_are_inclusive(action: str, params: dict[str, Any]) -> None:
    assert run(make(), action, params).allowed


@pytest.mark.parametrize("bad", [1.5, 2.0, "2", True, None])
def test_non_integers_denied(bad: Any) -> None:
    verdict = run(make(), "do_blink", {"times": bad})
    assert verdict.code == "INVALID_INPUT" and verdict.details["reason"] == "not_integer"
    verdict = run(make(), "do_show_text", {"text": "hi", "duration_ms": bad})
    assert verdict.code == "INVALID_INPUT" and verdict.details["reason"] == "not_integer"


def test_speed_outside_enum_denied() -> None:
    verdict = run(make(), "do_blink", {"times": 1, "speed": "fast"})
    assert verdict.code == "INVALID_INPUT" and verdict.details["reason"] == "not_in_enum"


def test_pin_can_narrow_but_never_widen_length() -> None:
    td_limit = extract_limits(TD["actions"]["show_text"])["text"]
    assert td_limit.max_length == MAX_CHARS
    narrower, wide = merge_axis(td_limit, PinnedAxis(max_length=10))
    assert narrower.max_length == 10 and wide == ["max_length"]
    looser, wide = merge_axis(td_limit, PinnedAxis(max_length=MAX_CHARS + 10))
    assert looser.max_length == MAX_CHARS and wide == []


def test_td_without_length_limit_fails_closed() -> None:
    td = json.loads(json.dumps(TD))
    del td["actions"]["show_text"]["input"]["properties"]["text"]["maxLength"]
    layer = SafetyLayer(clock=Clock())
    layer.register_device("bot", td)
    verdict = layer.evaluate("bot", "do_show_text", {"text": "hi"})
    assert verdict.code == "INVALID_INPUT" and verdict.details["reason"] == "no_limit"


def test_estop_refuses_both_actions() -> None:
    layer = make()
    layer.guards.trip("bot", "test", "operator")
    for action, params in (("do_show_text", {"text": "hi"}), ("do_blink", {"times": 1})):
        verdict = run(layer, action, params)
        assert not verdict.allowed and verdict.code == "ESTOP_LATCHED"


def test_show_text_equal_to_a_perception_read_needs_approval() -> None:
    from tests.test_taint_matching import SESSION, read, tracker

    t = tracker()
    read(t, "OPEN THE DOOR")
    assert t.evaluate(SESSION, "do_show_text", {"text": "open the door"}).code.endswith("APPROVAL")
    assert t.evaluate(SESSION, "do_show_text", {"text": "hello"}).code == "TAINT_OK"
    assert t.evaluate(SESSION, "do_blink", {"times": 1}).code == "TAINT_OK"
