"""Gateway process wiring: MQTT credentials, telemetry heartbeat, deadman sweep, human reset."""

import asyncio
import json
from pathlib import Path
from typing import Any, ClassVar

import paho.mqtt.client as paho_client
import pytest
from click.testing import CliRunner
from fastmcp import FastMCP

from tests.test_e2e_wiring import DEV, RecordingAudit, desk_td
from thingwire.cli import main
from thingwire.config import GatewayConfig
from thingwire.gateway_estop import ResetInbox, latched_scopes, request_outcome, request_reset
from thingwire.gateway_runtime import SafetyLoop, build_gateway, telemetry_hook
from thingwire.mqtt_bridge import MqttBridge
from thingwire.recorder import Recorder, read_recording
from thingwire.safety import SafetyLayer


class FakePaho:
    """Records the login and connects immediately."""

    instances: ClassVar[list["FakePaho"]] = []

    def __init__(self, **_: Any) -> None:
        self.login: tuple[str, str | None] | None = None
        self.on_connect: Any = None
        FakePaho.instances.append(self)

    def username_pw_set(self, user: str, password: str | None) -> None:
        self.login = (user, password)

    def reconnect_delay_set(self, **_: Any) -> None:
        pass

    def connect(self, *_: Any) -> None:
        pass

    def subscribe(self, _t: str) -> None:
        pass

    def loop_start(self) -> None:
        self.on_connect(self, None, None, 0)

    def loop_stop(self) -> None:
        pass

    def disconnect(self) -> None:
        pass


async def test_gateway_logs_in_with_configured_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(paho_client, "Client", FakePaho)
    monkeypatch.setenv("THINGWIRE_MQTT_USERNAME", "thingwire-gateway")
    monkeypatch.setenv("THINGWIRE_MQTT_PASSWORD", "s3cret")
    monkeypatch.setenv("THINGWIRE_MQTT_PORT", "1884")
    cfg = GatewayConfig()
    assert "s3cret" not in repr(cfg)  # SecretStr keeps it out of logs
    bridge = MqttBridge(cfg)
    await bridge.connect()
    assert FakePaho.instances[-1].login == ("thingwire-gateway", "s3cret")
    await bridge.disconnect()


async def test_anonymous_when_no_username(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paho_client, "Client", FakePaho)
    monkeypatch.delenv("THINGWIRE_MQTT_USERNAME", raising=False)
    bridge = MqttBridge(GatewayConfig())
    await bridge.connect()
    assert FakePaho.instances[-1].login is None


def test_telemetry_is_the_recorded_heartbeat(tmp_path: Path) -> None:
    now = [100.0]
    safety = SafetyLayer(clock=lambda: now[0])
    safety.register_device(DEV, desk_td())
    rec = Recorder(tmp_path / "s.mcap")
    bridge = MqttBridge(GatewayConfig())
    bridge.on_telemetry = telemetry_hook(safety, rec)
    readings = {"head_pan": {"value": 1.0, "unit": "degrees"}}
    bridge._handle_telemetry(DEV, json.dumps({"timestamp": "t", "readings": readings}).encode())
    bridge._handle_telemetry(DEV, json.dumps({"readings": readings}).encode())  # no heartbeat
    rec.close()
    channels = [e.channel for e in read_recording(tmp_path / "s.mcap")]
    assert channels.count("telemetry") == 2 and channels.count("heartbeat") == 1
    now[0] += 10_000  # long silence: the sweep latches the stop
    safety.sweep_deadman()
    assert safety.estop_latched(DEV)


def test_reset_inbox_applies_only_authorized_fresh_requests(tmp_path: Path) -> None:
    db = str(tmp_path / "state.db")
    wall = [1000.0]
    safety = SafetyLayer(state_db_path=db, authorized_resetters=["alice"])
    safety.register_device(DEV, desk_td())
    safety.trip_estop(DEV, "test", "test")
    inbox = ResetInbox(db, ttl_seconds=60, clock=lambda: wall[0])
    bad = request_reset(db, DEV, "mallory", "let me", clock=lambda: wall[0])
    old = request_reset(db, DEV, "alice", "old", clock=lambda: wall[0] - 120)
    assert dict(inbox.process(safety)) == {bad: "refused:RESET_NOT_AUTHORIZED", old: "expired"}
    assert safety.estop_latched(DEV) and latched_scopes(db)
    good = request_reset(db, DEV, "alice", "area clear", clock=lambda: wall[0])
    loop = SafetyLoop(safety, inbox, 1.0)
    loop.tick()
    assert request_outcome(db, good) == "reset" and not safety.estop_latched(DEV)
    assert latched_scopes(db) == []
    # the latch survives a restart until reset
    safety.trip_estop(DEV, "again", "test")
    assert SafetyLayer(state_db_path=db).estop_latched(DEV)


def test_cli_reset_files_a_request(tmp_path: Path) -> None:
    db = str(tmp_path / "state.db")
    res = CliRunner().invoke(
        main,
        [
            "estop",
            "reset",
            "--device",
            DEV,
            "--who",
            "alice",
            "--reason",
            "clear",
            "--wait",
            "0",
            "--state-db",
            db,
        ],
    )
    assert res.exit_code == 1 and "pending" in res.output
    safety = SafetyLayer(state_db_path=db, authorized_resetters=["alice"])
    assert ResetInbox(db, 60).process(safety) == [(1, "not_latched")]
    out = CliRunner().invoke(main, ["estop", "status", "--state-db", db])
    assert "No e-stop latched" in out.output


async def test_build_gateway_wires_adapter_and_env_resetters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THINGWIRE_ESTOP_RESETTERS", "alice, bob")
    cfg = GatewayConfig(
        safety_state_db_path=str(tmp_path / "s" / "state.db"),
        safety_config_path=str(tmp_path / "missing.yaml"),
        record_path=str(tmp_path / "r" / "run.mcap"),
        safety_sweep_seconds=0.01,
    )
    bridge = MqttBridge(cfg)
    published: list[tuple[str, str]] = []

    def publish(topic: str, payload: str, retain: bool = False, qos: int = 1) -> bool:
        published.append((topic, payload))
        return True

    bridge.publish_nowait = publish  # type: ignore[method-assign]
    gw = build_gateway(cfg, FastMCP("t"), bridge, RecordingAudit())  # type: ignore[arg-type]
    gw.start_background(asyncio.get_running_loop())
    bridge._devices[DEV] = desk_td()
    assert "do_look_at" in gw.register_device(DEV, desk_td())
    assert bridge.on_estop is not None and bridge.on_telemetry is not None
    bridge.on_estop(DEV, b"button", False)  # MQTT trip, as the bridge would route it
    assert gw.safety.estop_latched(DEV)
    assert gw.safety.config["global"]["estop"]["authorized_resetters"] == ["alice", "bob"]
    for _ in range(100):
        if any(t.endswith("/safety_status") for t, _ in published):
            break
        await asyncio.sleep(0.01)
    assert any(t == f"thingwire/{DEV}/safety_status" for t, _ in published)
    await gw.stop()
    assert any(e.channel == "td" for e in read_recording(cfg.record_path))
