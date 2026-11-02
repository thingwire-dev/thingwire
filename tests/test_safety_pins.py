"""Gateway side pinned policy: the stricter of pin and TD wins, widening TDs are flagged."""

import asyncio
import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

from tests.test_e2e_wiring import DEV, DeskBridge, build, call, desk_td
from thingwire.safety import SafetyLayer
from thingwire.safety_bridge import SafetyBridgeAdapter
from thingwire.safety_motion import AxisLimit
from thingwire.safety_pins import PinnedAxis, TdTrust, merge_axis

PINS: dict[str, Any] = {
    "unpinned": "auto",
    "devices": {
        DEV: {
            "actions": {
                "look_at": {
                    "inputs": {
                        "pan": {"minimum": -90, "maximum": 90, "max_slew_deg_per_sec": 90},
                        "tilt": {"minimum": -30, "maximum": 30, "max_slew_deg_per_sec": 90},
                    }
                },
                "set_expression": {
                    "inputs": {
                        "expression": {
                            "enum": ["neutral", "happy", "curious", "sleepy", "surprised", "alert"]
                        }
                    }
                },
                "nod": {"inputs": {"times": {"minimum": 1, "maximum": 3}}},
                "sweep": {
                    "requires_approval": True,
                    "inputs": {"speed_deg_s": {"minimum": 10, "maximum": 120}},
                },
            }
        }
    },
}


def layer_with(
    tmp_path: Path,
    trust: dict[str, Any] | None = PINS,
    clock: Callable[[], float] = lambda: 1000.0,
) -> SafetyLayer:
    cfg: dict[str, Any] = {
        "global": {"default_rate_limit": {"max_calls": 100, "window_seconds": 60}}
    }
    if trust is not None:
        cfg["td_trust"] = trust
    path = tmp_path / "safety.yaml"
    path.write_text(yaml.safe_dump(cfg))
    return SafetyLayer(str(path), clock=clock)


def widened() -> dict[str, Any]:
    td = desk_td()
    for axis in td["actions"]["look_at"]["input"]["properties"].values():
        axis["minimum"], axis["maximum"] = -900, 900
    return td


def events(layer: SafetyLayer) -> list[tuple[str, str | None]]:
    return [(f.event, f.axis) for f in layer.td_flags.history]


# --- merge rules -------------------------------------------------------------


def test_stricter_bound_wins_on_both_sides() -> None:
    pin = PinnedAxis(minimum=-90, maximum=90, max_slew_deg_per_sec=90)
    wide, why = merge_axis(AxisLimit(-900, 900, 500), pin)
    assert (wide.minimum, wide.maximum, wide.max_slew_deg_per_sec) == (-90, 90, 90)
    assert why == ["minimum", "maximum", "slew"]
    narrow, why = merge_axis(AxisLimit(-10, 10, 5), pin)
    assert (narrow.minimum, narrow.maximum, narrow.max_slew_deg_per_sec) == (-10, 10, 5)
    assert why == []


def test_a_bound_missing_from_the_td_is_widening_and_the_pin_applies() -> None:
    merged, why = merge_axis(AxisLimit(maximum=50), PinnedAxis(minimum=0, maximum=90))
    assert (merged.minimum, merged.maximum) == (0, 50)
    assert why == ["minimum"]


def test_axis_missing_from_the_td_takes_the_pin() -> None:
    merged, why = merge_axis(None, PinnedAxis(minimum=0, maximum=3))
    assert (merged.minimum, merged.maximum) == (0, 3) and why == ["minimum", "maximum"]


def test_enum_is_intersected_and_extra_members_are_widening() -> None:
    pin = PinnedAxis(enum=("a", "b"))
    merged, why = merge_axis(AxisLimit(enum=("a", "b", "evil")), pin)
    assert merged.enum == ("a", "b") and why == ["enum"]
    merged, why = merge_axis(AxisLimit(enum=("b",)), pin)
    assert merged.enum == ("b",) and why == []
    merged, why = merge_axis(AxisLimit(enum=("x",)), pin)
    assert merged.enum == () and why == ["enum"]  # nothing allowed: fail closed


def test_the_slew_history_key_of_the_td_is_kept() -> None:
    merged, _ = merge_axis(
        AxisLimit(-90, 90, 90, None, "head_pan"), PinnedAxis(max_slew_deg_per_sec=45)
    )
    assert (merged.max_slew_deg_per_sec, merged.slew_key) == (45, "head_pan")


# --- config ------------------------------------------------------------------


def test_good_config_parses() -> None:
    trust = TdTrust.from_config({"td_trust": PINS})
    assert trust.unpinned_mode() == "deny"
    assert trust.devices[DEV]["sweep"].requires_approval is True


@pytest.mark.parametrize(
    "bad",
    [
        {"unpinned": "maybe"},
        {"devices": []},
        {"devices": {"d": {"actions": {"a": {"inputs": {"x": {"minimum": "low"}}}}}}},
        {"devices": {"d": {"actions": {"a": {"inputs": {"x": {"minimum": True}}}}}}},
        {"devices": {"d": {"actions": {"a": {"inputs": {"x": {"enum": []}}}}}}},
        {"devices": {"d": {"actions": {"a": {"requires_approval": "yes"}}}}},
        {"devices": {"d": {"actions": {"a": {"inputs": {"x": {"minimum": float("nan")}}}}}}},
        {"devices": {"d": {"actions": {"a": {"inputs": {"x": {"bogus": 1}}}}}}},
    ],
)
def test_malformed_config_stops_the_gateway(bad: dict[str, Any], tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        layer_with(tmp_path, bad)


def test_unpinned_resolution() -> None:
    assert TdTrust.from_config({}).unpinned_mode() == "trust"
    assert TdTrust.from_config({"td_trust": {"devices": {}}}).unpinned_mode() == "trust"
    assert TdTrust.from_config({"td_trust": {"unpinned": "deny"}}).unpinned_mode() == "deny"
    assert TdTrust.from_config({"td_trust": PINS}).unpinned_mode() == "deny"
    pinned_trust = {"unpinned": "trust", "devices": PINS["devices"]}
    assert TdTrust.from_config({"td_trust": pinned_trust}).unpinned_mode() == "trust"


# --- TDP-03: a rogue TD widens the range ----------------------------------


def test_rogue_td_cannot_widen_a_pinned_range(tmp_path: Path) -> None:
    layer = layer_with(tmp_path)
    layer.register_device(DEV, widened())
    verdict = layer.evaluate(DEV, "do_look_at", {"pan": 400, "tilt": 0})
    assert (verdict.allowed, verdict.code) == (False, "OUT_OF_RANGE")
    assert layer.evaluate(DEV, "do_look_at", {"pan": 60, "tilt": 20}).allowed


def test_widening_emits_one_flag_per_axis_with_both_values(tmp_path: Path) -> None:
    layer = layer_with(tmp_path)
    layer.register_device(DEV, widened())
    assert events(layer) == [("td_limit_widened", "pan"), ("td_limit_widened", "tilt")]
    flag = layer.td_flags.history[0]
    assert flag.device_id == DEV and flag.action == "do_look_at"
    assert flag.detail["pinned"]["maximum"] == 90 and flag.detail["td"]["maximum"] == 900


def test_honest_td_emits_no_flag(tmp_path: Path) -> None:
    layer = layer_with(tmp_path)
    layer.register_device(DEV, desk_td())
    assert layer.td_flags.history == []
    assert layer.evaluate(DEV, "do_look_at", {"pan": 90, "tilt": 30}).allowed


def test_pinned_enum_beats_a_widened_td_enum(tmp_path: Path) -> None:
    td = desk_td()
    td["actions"]["set_expression"]["input"]["properties"]["expression"]["enum"].append("<script>")
    layer = layer_with(tmp_path)
    layer.register_device(DEV, td)
    assert not layer.evaluate(DEV, "do_set_expression", {"expression": "<script>"}).allowed
    assert layer.evaluate(DEV, "do_set_expression", {"expression": "happy"}).allowed
    assert ("td_limit_widened", "expression") in events(layer)


def test_pinned_slew_beats_a_td_that_drops_it(tmp_path: Path) -> None:
    td = desk_td()
    for prop in ("head_pan", "head_tilt"):
        td["properties"][prop]["maxSlewDegPerSec"] = 100000
    clock = [1000.0]
    layer = layer_with(tmp_path, clock=lambda: clock[0])
    layer.register_device(DEV, td)
    assert layer.evaluate(DEV, "do_look_at", {"pan": -80, "tilt": 0}).allowed
    clock[0] += 0.5
    assert layer.evaluate(DEV, "do_look_at", {"pan": 80, "tilt": 0}).code == "SLEW_EXCEEDED"


# --- TDP-04: a rogue TD drops the approval requirement ------------------------


def rogue_safe() -> dict[str, Any]:
    td = desk_td()
    td["actions"]["sweep"]["safe"] = True
    return td


def test_pin_keeps_the_approval_requirement(tmp_path: Path) -> None:
    layer = layer_with(tmp_path)
    layer.register_device(DEV, rogue_safe())
    assert layer.is_dangerous(DEV, "do_sweep")
    assert layer.evaluate(DEV, "do_sweep", {"speed_deg_s": 60}).code == "APPROVAL_REQUIRED"
    assert layer.evaluate(DEV, "do_sweep", {"speed_deg_s": 60}, approved=True).allowed
    assert events(layer) == [("td_approval_lowered", None)]


def test_td_can_add_an_approval_requirement_but_never_remove_one(tmp_path: Path) -> None:
    td = desk_td()
    td["actions"]["nod"]["safe"] = False
    layer = layer_with(tmp_path)
    layer.register_device(DEV, td)
    assert layer.is_dangerous(DEV, "do_nod")
    honest = layer_with(tmp_path)
    honest.register_device(DEV, desk_td())
    assert honest.is_dangerous(DEV, "do_sweep") and events(honest) == []


# --- unknown actions and devices --------------------------------------------


def test_action_the_pin_does_not_list_is_refused_and_flagged(tmp_path: Path) -> None:
    td = copy.deepcopy(desk_td())
    td["actions"]["unlock"] = {"safe": True, "input": {"type": "object", "properties": {}}}
    layer = layer_with(tmp_path)
    layer.register_device(DEV, td)
    assert layer.evaluate(DEV, "do_unlock", {}).code == "ACTION_NOT_ALLOWED"
    assert ("td_action_unpinned", None) in events(layer)
    assert layer.evaluate(DEV, "do_nod", {"times": 1}).allowed


def test_unpinned_device_is_denied_once_any_pin_exists(tmp_path: Path) -> None:
    layer = layer_with(tmp_path)
    layer.register_device("other-bot", desk_td())
    verdict = layer.evaluate("other-bot", "do_look_at", {"pan": 0, "tilt": 0})
    assert (verdict.allowed, verdict.code) == (False, "TD_NOT_PINNED")
    assert layer.evaluate("other-bot", "read_head_pan", {}).allowed


def test_unpinned_device_is_trusted_when_pinning_is_off(tmp_path: Path) -> None:
    layer = layer_with(tmp_path, None)
    layer.register_device("other-bot", desk_td())
    assert layer.evaluate("other-bot", "do_look_at", {"pan": 0, "tilt": 0}).allowed


def test_deny_with_no_readable_property_still_denies_everything(tmp_path: Path) -> None:
    layer = layer_with(tmp_path)
    layer.register_device("blank", {"actions": {"x": {"safe": True}}})
    assert layer.evaluate("blank", "do_x", {}).code == "TD_NOT_PINNED"


def test_pinned_device_with_only_unlisted_actions_denies_all(tmp_path: Path) -> None:
    layer = layer_with(tmp_path)
    layer.register_device(DEV, {"actions": {"zap": {"safe": True}}})
    assert layer.evaluate(DEV, "do_zap", {}).code == "ACTION_NOT_ALLOWED"


# --- audit trail ---------------------------------------------------------------


def test_listeners_get_past_and_future_flags(tmp_path: Path) -> None:
    layer = layer_with(tmp_path)
    layer.register_device(DEV, widened())
    seen: list[str] = []
    layer.td_flags.add_listener(lambda f: seen.append(f.event))
    assert seen == ["td_limit_widened", "td_limit_widened"]
    layer.register_device("dev-2", desk_td())
    assert seen[2:] == []  # unpinned device: denied, not flagged as widening


class Auditor:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def record_transition(self, t: Any) -> int:
        return 0

    async def record(self, *args: Any, **kw: Any) -> int:
        self.rows.append({"args": args, **kw})
        return len(self.rows)


class Pub:
    prefix = "thingwire"

    def publish_nowait(self, *a: Any, **k: Any) -> bool:
        return True


async def test_adapter_writes_a_flag_row_to_the_audit_log(tmp_path: Path) -> None:
    layer = layer_with(tmp_path)
    auditor = Auditor()
    SafetyBridgeAdapter(layer, Pub(), auditor, asyncio.get_running_loop())
    layer.register_device(DEV, rogue_safe())
    await asyncio.sleep(0.05)
    row = auditor.rows[0]
    assert row["args"][0] == DEV and row["args"][1] == "safety:td_approval_lowered"
    assert (row["source"], row["decision"], row["reason"]) == (
        "safety",
        "flag",
        "td_approval_lowered",
    )


# --- end to end through the gateway pipeline ---------------------------------


async def test_rogue_td_through_the_mcp_tool_never_reaches_the_device(tmp_path: Path) -> None:
    bridge = DeskBridge(widened())
    mcp, bridge, _, _ = build(layer_with(tmp_path), bridge)
    out = await call(mcp, "do_look_at", {"pan": 400, "tilt": 0})
    assert out["error"] == "OUT_OF_RANGE" and bridge.sent == []


async def test_rogue_safe_flag_through_the_mcp_tool_still_needs_approval(tmp_path: Path) -> None:
    bridge = DeskBridge(rogue_safe())
    mcp, bridge, _, _ = build(layer_with(tmp_path), bridge)
    out = await call(mcp, "do_sweep", {"speed_deg_s": 120})
    assert out["error"] == "APPROVAL_REQUIRED" and bridge.sent == []
