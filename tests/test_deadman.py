"""Deadman switch on the injected clock, including heartbeat replay."""

from pathlib import Path
from typing import Any

import pytest

from thingwire.recorder import FakeClock, Recorder, replay
from thingwire.safety import SafetyError, SafetyLayer

SEC = 1_000_000_000
TD: dict[str, Any] = {
    "properties": {},
    "actions": {"go": {"safe": True, "input": {"type": "object", "properties": {}}}},
}
IDENT: dict[str, Any] = {"agent_id": "a", "delegation_chain": ["u", "a"], "scopes": ["x:write"]}


def make_safety() -> tuple[SafetyLayer, FakeClock]:
    clock = FakeClock()
    return SafetyLayer(None, clock=clock), clock


def test_deadman_trips_after_timeout() -> None:
    safety, clock = make_safety()
    safety.record_heartbeat("d1")
    clock.now += 299
    safety.check_deadman_switch("d1")
    clock.now += 2
    with pytest.raises(SafetyError) as exc:
        safety.check_deadman_switch("d1")
    assert exc.value.code == "DEADMAN_SWITCH"


def test_heartbeat_resets_deadman() -> None:
    safety, clock = make_safety()
    safety.record_heartbeat("d1")
    clock.now += 250
    safety.record_heartbeat("d1")
    clock.now += 250
    safety.check_deadman_switch("d1")


class Ticker:
    t = 0

    def __call__(self) -> int:
        return self.t


def test_recorded_deadman_denial_replays_identically(tmp_path: Path) -> None:
    path = tmp_path / "dm.mcap"
    ticker = Ticker()
    rec = Recorder(path, clock_ns=ticker)
    safety, clock = make_safety()
    rec.record_td("d1", TD)
    safety.register_device("d1", TD)
    steps = [("hb", 0), ("call", 10), ("call", 400), ("hb", 410), ("call", 420)]
    for kind, sec in steps:
        ticker.t = sec * SEC
        clock.now = 1000.0 + sec
        if kind == "hb":
            rec.record_heartbeat("d1")
            safety.record_heartbeat("d1")
            continue
        seq = rec.record_tool_call("d1", "do_go", {}, **IDENT)
        decision = safety.evaluate("d1", "do_go", {}, **IDENT)
        rec.record_decision(seq, "d1", "do_go", decision, **IDENT)
    rec.close()
    first, second = replay(path), replay(path)
    codes = [d["code"] for _, d in sorted(first.decisions.items())]
    assert codes == ["OK", "DEADMAN_SWITCH", "OK"]
    assert first.decisions == second.decisions
    assert first.diffs == [] and second.diffs == []
