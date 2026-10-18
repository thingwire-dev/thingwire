"""Tests for the MCAP recorder and deterministic replay."""

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from mcap.reader import make_reader

from thingwire.cli import main
from thingwire.recorder import (
    FakeClock,
    Recorder,
    _patched_clock,
    decide,
    read_recording,
    replay,
)
from thingwire.safety import SafetyLayer

TD: dict[str, Any] = {"properties": {}, "actions": {"ping": {"safe": True}}}
SEC = 1_000_000_000


class Ticker:
    def __init__(self) -> None:
        self.t = 0

    def __call__(self) -> int:
        return self.t


def make_recording(path: Path) -> None:
    """Record a live session: 12 pings in 1s (rate limit), then a deadman gap."""
    ticker = Ticker()
    rec = Recorder(path, clock_ns=ticker)
    safety = SafetyLayer(None)
    clock = FakeClock()
    rec.record_td("d1", TD)
    safety.register_device("d1", TD)
    with _patched_clock(clock):
        schedule = [(i * SEC // 20, "telemetry" if i == 0 else "call") for i in range(13)]
        schedule.append((400 * SEC, "call"))
        for t, kind in schedule:
            ticker.t = t
            clock.now = 1000.0 + t / 1e9
            if kind == "telemetry":
                rec.record_telemetry("d1", {"temp": 21.5})
                safety.update_heartbeat_from_telemetry("d1", clock.now)
                continue
            seq = rec.record_tool_call("d1", "do_ping", {"n": t})
            allowed, code, msg = decide(safety, "d1", "do_ping")
            rec.record_decision(seq, "d1", "do_ping", allowed, code, msg)
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
    assert sum(e.channel == "tool_call" for e in events) == 13
    assert sum(e.channel == "decision" for e in events) == 13


def test_replay_matches_and_is_deterministic(mcap_path: Path) -> None:
    first = replay(mcap_path)
    second = replay(mcap_path)
    assert first.decisions == second.decisions
    assert first.diffs == [] and second.diffs == []
    codes = [d["code"] for _, d in sorted(first.decisions.items())]
    assert codes.count("RATE_LIMIT_EXCEEDED") == 2
    assert codes[-1] == "DEADMAN_SWITCH"
    assert len(first.device.commands) == 10


def test_replay_reports_diff_for_changed_config(mcap_path: Path, tmp_path: Path) -> None:
    cfg = tmp_path / "safety.yaml"
    cfg.write_text(
        "devices:\n  d1:\n    allowed_actions: [do_ping]\n    dangerous_actions: []\n"
        "    rate_limits:\n      do_ping: {max_calls: 3, window_seconds: 60}\n"
        "global: {deadman_switch_timeout_seconds: 300}\n"
    )
    result = replay(mcap_path, str(cfg))
    assert len(result.diffs) == 7
    assert all(d["replayed"]["code"] == "RATE_LIMIT_EXCEEDED" for d in result.diffs[:6])
    assert replay(mcap_path, str(cfg)).diffs == result.diffs


def test_cli_replay_exit_codes(mcap_path: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    ok = runner.invoke(main, ["replay", str(mcap_path)])
    assert ok.exit_code == 0 and "0 differences" in ok.output
    cfg = tmp_path / "s.yaml"
    cfg.write_text(
        "devices:\n  d1:\n    allowed_actions: [do_ping]\n"
        "    rate_limits:\n      do_ping: {max_calls: 1, window_seconds: 60}\n"
    )
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
