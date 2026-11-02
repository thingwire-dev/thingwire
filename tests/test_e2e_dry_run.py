"""Dry run (contracts 5.7): the verdict is real, the side effects are none."""

from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP

import thingwire.mcp_server as mcp_server
from tests.test_e2e_wiring import DEV, DeskBridge, RecordingAudit, build, call
from tests.test_identity_gate import FakeAuth, gatekeeper, ident
from thingwire.safety import SafetyLayer


class Clock:
    now = 1000.0

    def __call__(self) -> float:
        return self.now


def make(clock: Clock | None = None) -> tuple[FastMCP, DeskBridge, RecordingAudit, SafetyLayer]:
    return build(SafetyLayer(clock=clock or Clock()))


async def dry(mcp: Any, tool: str, **args: Any) -> dict[str, Any]:
    return await call(mcp, tool, {**args, "dry_run": True})


async def test_allowed_call_is_previewed_and_nothing_is_sent() -> None:
    mcp, bridge, audit, _ = make()
    out = await dry(mcp, "do_look_at", pan=10, tilt=-5)
    assert out["status"] == "dry_run" and out["would_allow"] and not out["needs_approval"]
    assert out["code"] == "OK" and out["value"] == {"pan": 10, "tilt": -5}
    assert out["not_evaluated"] == ["freshness"]
    assert bridge.sent == [] and bridge.bot.pan == 0.0
    assert [(r["decision"], r["reason"]) for r in audit.rows] == [("dry_run", "OK")]


async def test_real_denials_are_reported_with_their_code() -> None:
    mcp, bridge, _, _ = make()
    out = await dry(mcp, "do_look_at", pan=500, tilt=0)
    assert not out["would_allow"] and out["code"] == "OUT_OF_RANGE" and out["value"] is None
    assert bridge.sent == []


async def test_dry_run_does_not_advance_motion_history() -> None:
    clock = Clock()
    mcp, bridge, _, _ = make(clock)
    assert (await call(mcp, "do_look_at", {"pan": 0, "tilt": 0}))["status"] == "command_sent"
    clock.now += 1.0
    assert (await dry(mcp, "do_look_at", pan=85, tilt=0))["would_allow"]
    # had the dry run committed pan=85, going back to 0 in no time would be a slew breach
    assert (await call(mcp, "do_look_at", {"pan": 0, "tilt": 0}))["status"] == "command_sent"
    assert len(bridge.sent) == 2


async def test_dry_run_does_not_use_rate_limit_budget_but_sees_it_full() -> None:
    mcp, bridge, _, safety = make()[0], *make()[1:]
    mcp, bridge, _, safety = build(SafetyLayer(clock=Clock()))
    safety.config["global"]["default_rate_limit"] = {"max_calls": 2, "window_seconds": 60}
    for _ in range(5):
        assert (await dry(mcp, "do_nod", times=1))["would_allow"]
    assert (await call(mcp, "do_nod", {"times": 1}))["status"] == "command_sent"
    assert (await call(mcp, "do_nod", {"times": 1}))["status"] == "command_sent"
    out = await dry(mcp, "do_nod", times=1)
    assert not out["would_allow"] and out["code"] == "RATE_LIMIT_EXCEEDED"
    assert out["details"]["scope"] in ("action", "device")
    assert len(bridge.sent) == 2


async def test_denied_dry_runs_never_feed_the_flood_guard() -> None:
    mcp, _, _, _ = make()
    for _ in range(100):
        assert (await dry(mcp, "do_look_at", pan=500, tilt=0))["code"] == "OUT_OF_RANGE"
    assert (await call(mcp, "do_look_at", {"pan": 5, "tilt": 0}))["status"] == "command_sent"


async def test_latched_estop_is_reported_and_not_changed() -> None:
    mcp, bridge, _, safety = make()
    safety.trip_estop(DEV, "test", "operator")
    out = await dry(mcp, "do_nod", times=1)
    assert not out["would_allow"] and out["code"] == "ESTOP_LATCHED"
    assert bridge.sent == []


async def test_dangerous_action_in_stdio_mode_cannot_be_approved() -> None:
    mcp, bridge, _, _ = make()
    out = await dry(mcp, "do_sweep", speed_deg_s=30)
    assert out["needs_approval"] and out["code"] == "APPROVAL_REQUIRED"
    assert not out["would_allow"] and "identity is not configured" in out["message"]
    assert bridge.sent == []


async def test_dangerous_action_with_identity_asks_nobody(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mcp, bridge, _, _ = make()
    auth = FakeAuth([])
    mcp.gate = gatekeeper(auth)  # type: ignore[attr-defined]
    who = ident(f"device:{DEV}:read", f"device:{DEV}:act:sweep")
    monkeypatch.setattr(mcp_server, "current_identity", lambda: who)
    out = await dry(mcp, "do_sweep", speed_deg_s=30)
    assert out["would_allow"] and out["needs_approval"] and out["code"] == "APPROVAL_REQUIRED"
    assert auth.calls == [] and bridge.sent == []


async def test_missing_scope_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp, _, _, _ = make()
    mcp.gate = gatekeeper(FakeAuth([]))  # type: ignore[attr-defined]
    monkeypatch.setattr(mcp_server, "current_identity", lambda: ident(f"device:{DEV}:read"))
    out = await dry(mcp, "do_look_at", pan=1, tilt=1)
    assert not out["would_allow"] and out["code"] == "MISSING_SCOPE"


async def test_tainted_call_needs_approval_and_ledger_is_untouched(tmp_path: Path) -> None:
    cfg = tmp_path / "s.yaml"
    cfg.write_text("devices: {}\nglobal: {}\ntaint:\n  perception_properties: [head_pan]\n")
    mcp, bridge, _, _ = build(SafetyLayer(str(cfg)))
    bridge.readings["head_pan"] = 42.5
    await call(mcp, "read_head_pan", {})
    out = await dry(mcp, "do_look_at", pan=42.5, tilt=0)
    assert out["needs_approval"] and out["code"] == "TAINTED_CONTEXT_REQUIRES_APPROVAL"
    assert not out["would_allow"]  # stdio mode
    clean = await dry(mcp, "do_look_at", pan=12, tilt=0)
    assert clean["would_allow"] and not clean["needs_approval"]


async def test_offline_device_is_reported() -> None:
    bridge = DeskBridge()
    bridge.online = False
    mcp, bridge, _, _ = build(bridge=bridge)
    out = await dry(mcp, "do_nod", times=1)
    assert not out["would_allow"] and out["code"] == "DEVICE_OFFLINE"


async def test_dry_run_false_and_omitted_run_the_real_call() -> None:
    mcp, bridge, _, _ = make()
    assert (await call(mcp, "do_nod", {"times": 1, "dry_run": False}))["status"] == "command_sent"
    assert (await call(mcp, "do_nod", {"times": 1}))["status"] == "command_sent"
    assert len(bridge.sent) == 2
