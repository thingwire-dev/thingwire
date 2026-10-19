"""Gateway wiring: TD action to MQTT command, fail-closed approvals, taint, recorder, e-stop."""

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp import Client, FastMCP

import thingwire.mcp_server as mcp_server
from tests.test_identity_gate import GRANTED, FakeAuth, gatekeeper, ident
from thingwire.config import GatewayConfig
from thingwire.gateway_actions import ActionPipeline
from thingwire.gateway_taint import TaintTracker
from thingwire.mcp_server import (
    attach_pipeline,
    command_route,
    register_device_tools,
    register_meta_tools,
)
from thingwire.mqtt_bridge import ConnectionState, MqttBridge
from thingwire.recorder import Recorder, read_recording, replay
from thingwire.safety import SafetyLayer
from thingwire.td_loader import parse_thing_description_dict

ROOT = Path(__file__).resolve().parent.parent
DEV = "desk-bot"
_spec = importlib.util.spec_from_file_location(
    "virtual_desk_bot", ROOT / "scripts" / "virtual_desk_bot.py"
)
assert _spec and _spec.loader
bot_mod = sys.modules.get("virtual_desk_bot") or importlib.util.module_from_spec(_spec)
if "virtual_desk_bot" not in sys.modules:
    sys.modules["virtual_desk_bot"] = bot_mod
    _spec.loader.exec_module(bot_mod)


def desk_td(device_id: str = DEV) -> dict[str, Any]:
    """The library desk bot TD as the device publishes it."""
    raw = (ROOT / "td-library" / "desk-bot.td.json").read_text()
    return json.loads(raw.replace("{device_id}", device_id).replace("{broker}", "b"))  # type: ignore[no-any-return]


class RecordingAudit:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def record(self, **row: Any) -> int:
        self.rows.append(row)
        return len(self.rows)

    async def get_recent(self, device_id: str | None = None, limit: int = 20) -> list[Any]:
        return self.rows[-limit:]


class DeskBridge:
    """Bridge stand-in backed by the virtual desk bot: commands really move the bot."""

    def __init__(self, td: dict[str, Any] | None = None) -> None:
        self.td = td or desk_td()
        self.bot = bot_mod.DeskBot({"people": []})
        self.sent: list[dict[str, Any]] = []
        self.acks: list[dict[str, Any]] = []
        self.readings: dict[str, Any] = {}
        self.online = True

    def is_device_online(self, _d: str) -> bool:
        return self.online

    def get_devices(self) -> list[str]:
        return [DEV]

    def get_td(self, _d: str) -> Any:
        return parse_thing_description_dict(self.td)

    def get_device_status(self, _d: str) -> str:
        return "online"

    def get_latest_reading(self, device_id: str, name: str) -> dict[str, Any]:
        return {"device_id": device_id, "property": name, "value": self.readings.get(name, 0.0)}

    async def send_command(self, device_id: str, target: str, command: str, value: Any) -> str:
        cmd = {
            "action_id": f"a{len(self.sent)}",
            "target": target,
            "command": command,
            "value": value,
        }
        self.sent.append(cmd)
        self.acks.append(self.bot.apply_command(cmd))
        return str(cmd["action_id"])


def build(
    safety: SafetyLayer | None = None,
    bridge: DeskBridge | None = None,
    recorder: Recorder | None = None,
) -> tuple[FastMCP, DeskBridge, RecordingAudit, SafetyLayer]:
    """Stdio-mode server (no identity) with the desk bot keyed by its MQTT id."""
    mcp, bridge = FastMCP("t"), bridge or DeskBridge()
    audit, safety = RecordingAudit(), safety or SafetyLayer()
    safety.register_device(DEV, bridge.td)
    taint = TaintTracker.from_config(safety.config)
    attach_pipeline(mcp, ActionPipeline(bridge, safety, audit, recorder=recorder, taint=taint))  # type: ignore[arg-type]
    register_device_tools(mcp, bridge.get_td(DEV), bridge, safety, audit, device_id=DEV)  # type: ignore[arg-type]
    register_meta_tools(mcp, bridge, audit, safety)  # type: ignore[arg-type]
    return mcp, bridge, audit, safety


async def call(mcp: FastMCP, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    async with Client(mcp) as c:
        res = await c.call_tool(tool, args, raise_on_error=False)
    return dict(res.structured_content or {})


class TestCommandMapping:
    async def test_td_action_maps_to_mqtt_command(self) -> None:
        """do_look_at publishes the TD action name with the validated input object."""
        cfg = GatewayConfig(device_topic_prefix="thingwire")
        bridge = MqttBridge(cfg)
        bridge._client = MagicMock()
        bridge._client.publish.return_value.rc = 0
        bridge._state = ConnectionState.CONNECTED
        bridge._devices[DEV] = desk_td()
        safety, audit, mcp = SafetyLayer(), RecordingAudit(), FastMCP("t")
        safety.register_device(DEV, desk_td())
        register_device_tools(mcp, bridge.get_td(DEV), bridge, safety, audit, device_id=DEV)  # type: ignore[arg-type]
        out = await call(mcp, "do_look_at", {"pan": 10, "tilt": -5})
        assert out["status"] == "command_sent", out
        topic, payload = bridge._client.publish.call_args.args
        cmd = json.loads(payload)
        assert topic == f"thingwire/{DEV}/command"
        assert (cmd["target"], cmd["command"]) == ("look_at", "look_at")
        assert cmd["value"] == {"pan": 10, "tilt": -5}
        ack = bot_mod.DeskBot({"people": []}).apply_command(cmd)  # the device accepts it
        assert ack["status"] == "ok" and ack["action"] == "look_at"

    def test_form_may_declare_target_and_command(self) -> None:
        td = desk_td()
        td["actions"]["look_at"]["forms"][0].update(
            {"thingwire:target": "head", "thingwire:command": "point"}
        )
        route = command_route(parse_thing_description_dict(td), "look_at")
        assert (route.target, route.command) == ("head", "point")
        assert command_route(parse_thing_description_dict(desk_td()), "nod").target == "nod"

    async def test_clamped_params_are_what_is_sent(self, tmp_path: Path) -> None:
        cfg = tmp_path / "safety.yaml"
        cfg.write_text("devices: {}\nglobal:\n  out_of_range: clamp\n")
        mcp, bridge, _, _ = build(SafetyLayer(str(cfg)))
        out = await call(mcp, "do_look_at", {"pan": 500, "tilt": 0})
        assert out["code"] == "RANGE_CLAMPED" and bridge.sent[-1]["value"]["pan"] == 90
        assert bridge.acks[-1]["status"] == "ok"


class TestFailClosed:
    async def test_dangerous_without_identity_is_denied(self) -> None:
        mcp, bridge, audit, _ = build()
        out = await call(mcp, "do_sweep", {"speed_deg_s": 30})
        assert out["error"] == "APPROVAL_REQUIRED" and not bridge.sent
        assert audit.rows[-1]["decision"] == "deny"
        assert audit.rows[-1]["reason"] == "APPROVAL_REQUIRED"

    async def test_approved_argument_never_approves(self) -> None:
        td = desk_td()
        td["actions"]["sweep"]["input"]["properties"]["approved"] = {"type": "boolean"}
        mcp, bridge, _, _ = build(bridge=DeskBridge(td))
        out = await call(mcp, "do_sweep", {"speed_deg_s": 30, "approved": True})
        assert out["error"] == "APPROVAL_REQUIRED" and not bridge.sent

    async def test_out_of_range_denied_and_not_sent(self) -> None:
        mcp, bridge, audit, _ = build()
        out = await call(mcp, "do_look_at", {"pan": 500, "tilt": 0})
        assert out["error"] == "OUT_OF_RANGE" and not bridge.sent
        assert audit.rows[-1]["reason"] == "OUT_OF_RANGE"


class TestEstopTool:
    async def test_agent_can_trip_but_not_reset(self) -> None:
        mcp, bridge, _, safety = build()
        out = await call(mcp, "emergency_stop", {"device_id": DEV, "reason": "hand in the way"})
        assert out["status"] == "estop_latched" and safety.estop_latched(DEV)
        denied = await call(mcp, "do_look_at", {"pan": 1, "tilt": 0})
        assert denied["error"] == "ESTOP_LATCHED" and not bridge.sent
        async with Client(mcp) as c:
            names = [t.name for t in await c.list_tools()]
        assert "emergency_stop" in names
        assert not [n for n in names if "reset" in n or "clear" in n or "latch" in n]
        assert (await call(mcp, "emergency_stop", {"device_id": "nope"}))["error"] == (
            "DEVICE_NOT_REGISTERED"
        )

    async def test_stop_needs_read_scope_with_identity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mcp, _, _, safety = build()
        mcp.gate = gatekeeper(FakeAuth([GRANTED]))  # type: ignore[attr-defined]
        monkeypatch.setattr(mcp_server, "current_identity", lambda: ident())
        out = await call(mcp, "emergency_stop", {"device_id": DEV})
        assert out["reason"] == "missing scope" and not safety.estop_latched(DEV)


class TestRecorder:
    async def test_every_decision_recorded_and_replay_matches(self, tmp_path: Path) -> None:
        path = tmp_path / "s.mcap"
        rec = Recorder(path)
        mcp, _, _, _ = build(recorder=rec)
        rec.record_td(DEV, desk_td())
        await call(mcp, "do_look_at", {"pan": 10, "tilt": 0})
        await call(mcp, "do_look_at", {"pan": 500, "tilt": 0})
        await call(mcp, "do_sweep", {"speed_deg_s": 30})  # denied before the safety layer
        rec.close()
        events = read_recording(path)
        calls = [e.data for e in events if e.channel == "tool_call"]
        decisions = [e.data for e in events if e.channel == "decision"]
        assert len(calls) == len(decisions) == 3
        assert [d["code"] for d in decisions] == ["OK", "OUT_OF_RANGE", "APPROVAL_REQUIRED"]
        assert calls[2]["stage"] == "approval"
        result = replay(path)
        assert result.diffs == [] and len(result.decisions) == 2


class TestTaint:
    @pytest.fixture
    def taint_cfg(self, tmp_path: Path) -> str:
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(
            "devices: {}\nglobal: {}\ntaint:\n  perception_properties: [head_pan]\n"
            "  scans:\n    head_tilt: {range_min: 0, range_max: 10, max_change_m_per_s: 5,"
            " sectors: 2}\n"
        )
        return str(cfg)

    async def test_sensor_value_in_action_needs_approval(self, taint_cfg: str) -> None:
        mcp, bridge, _, _ = build(SafetyLayer(taint_cfg))
        bridge.readings["head_pan"] = 42.5
        read = await call(mcp, "read_head_pan", {})
        assert read["origin"] == "untrusted_perception" and read["taint_id"].startswith("t_")
        out = await call(mcp, "do_look_at", {"pan": 42.5, "tilt": 0})
        assert out["error"] == "APPROVAL_REQUIRED" and not bridge.sent  # stdio cannot approve
        assert (await call(mcp, "do_look_at", {"pan": 12, "tilt": 0}))["status"] == "command_sent"

    async def test_override_scope_and_real_approval(
        self, taint_cfg: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mcp, bridge, _, _ = build(SafetyLayer(taint_cfg))
        auth = FakeAuth([GRANTED])
        mcp.gate = gatekeeper(auth)  # type: ignore[attr-defined]
        who = ident(f"device:{DEV}:read", f"device:{DEV}:act:look_at")
        monkeypatch.setattr(mcp_server, "current_identity", lambda: who)
        bridge.readings["head_pan"] = 33.0
        await call(mcp, "read_head_pan", {})
        out = await call(mcp, "do_look_at", {"pan": 33, "tilt": 0})
        assert out["status"] == "command_sent" and len(auth.calls) == 2  # open + spend

    async def test_implausible_scan_is_untrusted(self, taint_cfg: str) -> None:
        mcp, bridge, _, _ = build(SafetyLayer(taint_cfg))
        bridge.readings["head_tilt"] = [4.0, 4.0, 4.0, 4.0]
        read = await call(mcp, "read_head_tilt", {})
        assert read["plausible"] is False and "SCAN_ALL_IDENTICAL" in read["plausibility_findings"]
        assert read["origin"] == "untrusted_perception"
