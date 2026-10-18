"""Tests for the MCAP recorder and deterministic replay through SafetyLayer.evaluate."""

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from mcap.reader import make_reader

from thingwire.cli import main
from thingwire.recorder import FakeClock, Recorder, read_recording, replay
from thingwire.safety import SafetyLayer

TD: dict[str, Any] = {
    "properties": {},
    "actions": {
        "setAngle": {
            "safe": True,
            "input": {
                "type": "object",
                "properties": {
                    "angle": {"type": "number", "minimum": 0, "maximum": 180, "unit": "deg"}
                },
            },
        }
    },
}
SEC = 1_000_000_000
ACTION = "do_setAngle"
IDENT: dict[str, Any] = {
    "agent_id": "agent-1",
    "delegation_chain": ["user", "agent-1"],
    "scopes": ["servo:write"],
}


class Ticker:
    def __init__(self) -> None:
        self.t = 0

    def __call__(self) -> int:
        return self.t


def make_recording(path: Path) -> None:
    """Live session: in-range, out-of-range, slew violation, then a 12-call burst."""
    ticker = Ticker()
    rec = Recorder(path, clock_ns=ticker)
    clock = FakeClock()
    safety = SafetyLayer(None, clock=clock)
    rec.record_td("d1", TD)
    safety.register_device("d1", TD)
    schedule = [(0, 90.0), (SEC // 10, 200.0), (SEC // 5, 170.0)]
    schedule += [(2 * SEC + i * SEC // 20, 90.0) for i in range(12)]
    for t, angle in schedule:
        ticker.t = t
        clock.now = 1000.0 + t / 1e9
        params = {"angle": angle}
        seq = rec.record_tool_call("d1", ACTION, params, **IDENT)
        decision = safety.evaluate("d1", ACTION, params, **IDENT)
        rec.record_decision(seq, "d1", ACTION, decision, **IDENT)
    rec.record_telemetry("d1", {"temp": 21.5})
    rec.close()


@pytest.fixture
def mcap_path(tmp_path: Path) -> Path:
    p = tmp_path / "session.mcap"
    make_recording(p)
    return p


def test_mcap_readable_with_all_channels(mcap_path: Path) -> None:
    with open(mcap_path, "rb") as f:
        summary = make_reader(f).get_summary()
    assert summary is not None
    topics = {c.topic for c in summary.channels.values()}
    assert topics == {"tool_call", "decision", "telemetry", "td"}
    for schema in summary.schemas.values():
        assert schema.encoding == "jsonschema"
    events = read_recording(mcap_path)
    assert sum(e.channel == "tool_call" for e in events) == 15
    assert sum(e.channel == "decision" for e in events) == 15


def test_decisions_carry_fields_and_identity(mcap_path: Path) -> None:
    decisions = [e.data for e in read_recording(mcap_path) if e.channel == "decision"]
    assert decisions[1]["allowed"] is False
    assert decisions[1]["code"] == "OUT_OF_RANGE" and decisions[1]["message"]
    assert decisions[0]["agent_id"] == "agent-1"
    assert decisions[0]["delegation_chain"] == ["user", "agent-1"]
    assert decisions[0]["scopes"] == ["servo:write"]


def test_replay_matches_and_is_deterministic(mcap_path: Path) -> None:
    first = replay(mcap_path)
    second = replay(mcap_path)
    assert first.decisions == second.decisions
    assert first.diffs == [] and second.diffs == []
    codes = [d["code"] for _, d in sorted(first.decisions.items())]
    assert codes[:3] == ["OK", "OUT_OF_RANGE", "SLEW_EXCEEDED"]
    assert codes.count("RATE_LIMIT_EXCEEDED") == 3
    assert len(first.device.commands) == 10


def test_replay_reports_diff_for_changed_config(mcap_path: Path, tmp_path: Path) -> None:
    cfg = tmp_path / "safety.yaml"
    cfg.write_text(
        "global:\n  out_of_range: clamp\n  default_rate_limit: {max_calls: 3, window_seconds: 60}\n"
    )
    result = replay(mcap_path, str(cfg))
    changed = {d["call_seq"]: d["replayed"]["code"] for d in result.diffs}
    assert changed[1] == "SLEW_EXCEEDED"  # clamped, then caught by slew
    assert list(changed.values()).count("RATE_LIMIT_EXCEEDED") == 7
    assert len(result.diffs) > 5
    assert all(d["recorded"] != d["replayed"] for d in result.diffs)
    assert replay(mcap_path, str(cfg)).diffs == result.diffs


def test_cli_replay_exit_codes(mcap_path: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    ok = runner.invoke(main, ["replay", str(mcap_path)])
    assert ok.exit_code == 0 and "0 differences" in ok.output
    cfg = tmp_path / "s.yaml"
    cfg.write_text("global:\n  default_rate_limit: {max_calls: 1, window_seconds: 60}\n")
    bad = runner.invoke(main, ["replay", str(mcap_path), "--safety-config", str(cfg)])
    assert bad.exit_code == 1 and "differences" in bad.output


def test_recorder_drops_instead_of_raising(tmp_path: Path) -> None:
    rec = Recorder(tmp_path / "x.mcap", max_queue=1)
    rec.record_telemetry("d1", {"bad": object()})  # serialized via default=str
    for _ in range(200):
        rec.record_telemetry("d1", {"v": 1})
    assert rec.drops > 0
    rec.close()
    rec.record_td("d1", TD)  # after close: dropped, no raise
    assert rec.drops > 1
    rec.close()
