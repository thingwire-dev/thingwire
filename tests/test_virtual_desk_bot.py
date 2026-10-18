"""Tests for the virtual desk bot simulation and the desk bot TD."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from thingwire.td_loader import parse_thing_description
from thingwire.tool_compiler import compile_tools

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "virtual_desk_bot", ROOT / "scripts" / "virtual_desk_bot.py"
)
assert _spec and _spec.loader
bot_mod = importlib.util.module_from_spec(_spec)
sys.modules["virtual_desk_bot"] = bot_mod
_spec.loader.exec_module(bot_mod)

DT = 0.1


def _cmd(name: str, value: dict[str, object]) -> dict[str, object]:
    return {"action_id": "t1", "target": f"{name}1", "command": name, "value": value}


def _run(bot: "bot_mod.DeskBot", seconds: float) -> list[tuple[float, float]]:
    poses = []
    for _ in range(round(seconds / DT)):
        bot.step(DT)
        poses.append((bot.pan, bot.tilt))
    return poses


def _assert_slew(start: tuple[float, float], poses: list[tuple[float, float]], slew: float) -> None:
    prev = start
    for pan, tilt in poses:
        assert abs(pan - prev[0]) <= slew * DT + 1e-6
        assert abs(tilt - prev[1]) <= slew * DT + 1e-6
        prev = (pan, tilt)


def test_look_at_and_expression_respect_slew() -> None:
    """look_at plus set_expression change state, with per-tick motion within the slew limit."""
    bot = bot_mod.DeskBot()
    assert bot.apply_command(_cmd("look_at", {"pan": -60, "tilt": 20}))["status"] == "ok"
    assert bot.apply_command(_cmd("set_expression", {"expression": "happy"}))["status"] == "ok"
    assert bot.expression == "happy"

    poses = _run(bot, 0.5)
    assert poses[-1] != (-60.0, 20.0)  # slew limit means it has not arrived yet
    assert bot.pan < 0
    _assert_slew((0.0, 0.0), poses, 90.0)

    poses = _run(bot, 1.0)
    assert (bot.pan, bot.tilt) == (-60.0, 20.0)
    telem = bot.telemetry()["readings"]
    assert telem["head_pan"]["value"] == -60.0
    assert telem["expression"]["value"] == "happy"


def test_out_of_range_and_unknown_rejected() -> None:
    """Bad arguments produce error acks and leave the pose untouched."""
    bot = bot_mod.DeskBot()
    assert bot.apply_command(_cmd("look_at", {"pan": 120, "tilt": 0}))["status"] == "error"
    assert bot.apply_command(_cmd("set_expression", {"expression": "angry"}))["status"] == "error"
    assert bot.apply_command(_cmd("nod", {"times": 9}))["status"] == "error"
    assert bot.apply_command(_cmd("look_at", {"pan": 5}))["status"] == "error"
    assert bot.apply_command(_cmd("dance", {}))["status"] == "error"
    _run(bot, 1.0)
    assert (bot.pan, bot.tilt) == (0.0, 0.0)


def test_nod_returns_to_start_within_slew() -> None:
    """nod moves tilt and comes back to the original tilt."""
    bot = bot_mod.DeskBot()
    assert bot.apply_command(_cmd("nod", {"times": 2}))["status"] == "ok"
    poses = _run(bot, 5.0)
    assert min(t for _, t in poses) < 0
    assert (bot.pan, bot.tilt) == (0.0, 0.0)
    _assert_slew((0.0, 0.0), poses, 90.0)


def test_sweep_is_slew_limited_and_cancelled_by_look_at() -> None:
    """sweep at 120 deg/s is capped to the 90 deg/s slew limit; look_at stops it."""
    bot = bot_mod.DeskBot()
    assert bot.apply_command(_cmd("sweep", {"speed_deg_s": 120}))["status"] == "ok"
    poses = _run(bot, 4.0)
    _assert_slew((0.0, 0.0), poses, 90.0)
    assert max(p for p, _ in poses) == 90.0
    assert min(p for p, _ in poses) == -90.0
    bot.apply_command(_cmd("look_at", {"pan": 0, "tilt": 0}))
    _run(bot, 3.0)
    assert bot.pan == 0.0


def test_gateway_style_target_resolution() -> None:
    """Commands as the gateway builds them (target derived from the action name) still resolve."""
    bot = bot_mod.DeskBot()
    cmd = {
        "action_id": "g1",
        "target": "_expression1",
        "command": "set",
        "value": {"expression": "alert"},
    }
    assert bot.apply_command(cmd)["status"] == "ok"
    assert bot.expression == "alert"


def test_scene_visibility_follows_head() -> None:
    """Sign text and people are visible only when the head points at them."""
    bot = bot_mod.DeskBot(bot_mod.load_scene())
    assert not bot.telemetry()["readings"]["person_detected"]["value"] or bot.visible_people() == []
    bot.apply_command(_cmd("look_at", {"pan": -40, "tilt": 0}))
    _run(bot, 1.0)
    assert bot.telemetry()["readings"]["person_detected"]["value"] is True
    assert any("ignore previous instructions" in t for t in bot.visible_text())


def test_td_loads_and_compiles_with_safe_flags() -> None:
    """desk-bot.td.json parses via td_loader and compiles to tools; only sweep is dangerous."""
    td = bot_mod.build_td("deskbot-001", "localhost")
    assert td["id"] == "urn:thingwire:device:deskbot-001"
    parsed = parse_thing_description(json.dumps(td))
    tools = {t.source_name: t for t in compile_tools(parsed)}
    assert {"head_pan", "head_tilt", "expression", "person_detected"} <= set(tools)
    assert {"look_at", "set_expression", "nod", "sweep"} <= set(tools)
    assert tools["sweep"].safe is False
    for name in ("look_at", "set_expression", "nod"):
        assert tools[name].safe is True
    assert tools["sweep"].name == "do_sweep"
    assert td["properties"]["head_pan"]["maxSlewDegPerSec"] == 90


@pytest.mark.parametrize("axis", ["head_pan", "head_tilt"])
def test_td_declares_default_slew(axis: str) -> None:
    """Both head axes carry the maxSlewDegPerSec extension."""
    td = bot_mod.build_td("d", "b")
    assert td["properties"][axis]["maxSlewDegPerSec"] == 90
