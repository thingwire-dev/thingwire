"""Status payload, transitions, audit trail, MQTT adapter and desk bot e-stop tests."""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from thingwire.audit_log import AuditLog
from thingwire.safety_bridge import SafetyBridgeAdapter, is_reset_payload, status_topic

from .estop_helpers import make_layer

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "virtual_desk_bot_estop", ROOT / "scripts" / "virtual_desk_bot.py"
)
assert _spec and _spec.loader
bot_mod = importlib.util.module_from_spec(_spec)
sys.modules["virtual_desk_bot_estop"] = bot_mod
_spec.loader.exec_module(bot_mod)


class FakeBridge:
    prefix = "thingwire"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, bool]] = []

    def publish_nowait(self, topic: str, payload: str, retain: bool = False, qos: int = 1) -> bool:
        self.sent.append((topic, payload, retain))
        return True


def test_status_payload_fields_and_sequence(tmp_path: Path) -> None:
    layer, _, events = make_layer(tmp_path, threshold=2)
    layer.trip_estop("servo", "button", source="mqtt")
    layer.evaluate("servo", "do_setAngle", {"angle": 1})
    layer.reset_estop("servo", "ok now", "operator")
    steps = [(e.event, e.state.value, e.drop_reason) for e in events if e.device_id == "servo"]
    assert steps == [
        ("registered", "ok", None),
        ("estop_tripped", "estop", "ESTOP_LATCHED"),
        ("estop_reset", "ok", None),
    ]
    payload = events[1].payload()
    assert set(payload) == {
        "state",
        "drop_reason",
        "last_decision_code",
        "trace_id",
        "timestamp",
    }
    assert payload["state"] == "estop" and payload["trace_id"].startswith("trace-")
    assert payload["timestamp"].endswith("Z")


def test_global_estop_fans_out_to_all_devices(tmp_path: Path) -> None:
    layer, _, events = make_layer(tmp_path)
    layer.trip_estop(None, "everything stops")
    scopes = {e.device_id for e in events if e.state.value == "estop"}
    assert scopes == {None, "servo"}
    assert status_topic("thingwire", None) == "thingwire/safety_status"
    assert status_topic("thingwire", "servo") == "thingwire/servo/safety_status"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [(b'{"reset": true}', True), (b"reset", False), (b"", False), (b'{"reset": 1}', False)],
)
def test_reset_payload_detection(payload: bytes, expected: bool) -> None:
    assert is_reset_payload(payload) is expected


async def test_adapter_publishes_retained_and_audits(tmp_path: Path) -> None:
    layer, _, _ = make_layer(tmp_path)
    audit = AuditLog(":memory:")
    await audit.initialize()
    bridge = FakeBridge()
    adapter = SafetyBridgeAdapter(layer, bridge, audit, asyncio.get_running_loop())
    adapter.on_estop_message("servo", b"hit the button", False)
    adapter.on_estop_message("servo", b"stale retained", True)  # ignored
    adapter.on_estop_message(None, b'{"reset": true}', False)  # echo, ignored
    layer.reset_estop("servo", "inspected", "operator")
    for _ in range(50):
        await asyncio.sleep(0.01)
        if len(bridge.sent) >= 3:
            break
    topics = [(t, json.loads(p)["state"], r) for t, p, r in bridge.sent if "safety_status" in t]
    assert topics == [
        ("thingwire/servo/safety_status", "estop", True),
        ("thingwire/servo/safety_status", "ok", True),
    ]
    assert ("thingwire/servo/estop", '{"reset": true}', False) in bridge.sent
    rows: list[dict[str, Any]] = await audit.get_recent("servo")
    by_action = {r["action"]: r for r in rows}
    assert by_action["safety:estop_tripped"]["agent_id"] == "mqtt"
    assert by_action["safety:estop_reset"]["agent_id"] == "operator"
    assert by_action["safety:estop_reset"]["result"]["detail"] == "inspected"
    assert by_action["safety:estop_reset"]["decision"] == "transition"
    await audit.close()


def test_desk_bot_halts_parks_and_ignores_until_reset() -> None:
    bot = bot_mod.DeskBot()
    look = {"action_id": "a", "command": "look_at", "value": {"pan": 60, "tilt": 20}}
    assert bot.apply_command(look)["status"] == "ok"
    for _ in range(5):
        bot.step(0.1)
    assert bot.pan > 0
    assert bot.handle_estop_payload(b"stop") == "tripped"
    for _ in range(30):
        bot.step(0.1)
    assert (bot.pan, bot.tilt) == (0.0, 0.0)  # parked
    ack = bot.apply_command(look)
    assert (ack["status"], ack["error"]) == ("error", "ESTOP_LATCHED")
    assert bot.handle_estop_payload(b'{"reset": true}') == "reset"
    assert bot.apply_command(look)["status"] == "ok"


def test_desk_bot_estop_cancels_sweep() -> None:
    bot = bot_mod.DeskBot()
    bot.apply_command({"command": "sweep", "value": {"speed_deg_s": 60}})
    bot.step(0.5)
    bot.trip_estop()
    for _ in range(40):
        bot.step(0.1)
    assert bot.pan == 0.0 and bot._sweep_speed is None
