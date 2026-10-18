"""MCAP session recorder and deterministic replay.

Channels: tool_call, decision, telemetry, td (JSON, Foxglove compatible).
The recorder never blocks the event loop and never raises into the safety path:
records go to a bounded queue drained by a writer thread; failures are logged
and counted in ``drops``.
"""

import json
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from mcap.reader import make_reader
from mcap.writer import Writer

from thingwire.safety import SafetyLayer
from thingwire.safety_motion import Decision

logger = logging.getLogger(__name__)

_STR = {"type": "string"}
_OBJ = {"type": "object"}
_NUM = {"type": "number"}
SCHEMAS: dict[str, dict[str, Any]] = {
    "tool_call": {
        "type": "object",
        "properties": {
            "seq": _NUM,
            "device_id": _STR,
            "action": _STR,
            "params": _OBJ,
            "approved": {"type": "boolean"},
            "agent_id": _STR,
            "delegation_chain": {"type": "array", "items": _STR},
            "scopes": {"type": "array", "items": _STR},
        },
    },
    "decision": {
        "type": "object",
        "properties": {
            "call_seq": _NUM,
            "device_id": _STR,
            "action": _STR,
            "allowed": {"type": "boolean"},
            "code": _STR,
            "message": _STR,
            "agent_id": _STR,
            "delegation_chain": {"type": "array", "items": _STR},
            "scopes": {"type": "array", "items": _STR},
        },
    },
    "telemetry": {
        "type": "object",
        "properties": {"device_id": _STR, "readings": _OBJ},
    },
    "td": {"type": "object", "properties": {"device_id": _STR, "td": _OBJ}},
}
_STOP = object()


def _put_identity(
    payload: dict[str, Any],
    agent_id: str | None,
    delegation_chain: list[str] | None,
    scopes: list[str] | None,
) -> None:
    if agent_id is not None:
        payload["agent_id"] = agent_id
    if delegation_chain is not None:
        payload["delegation_chain"] = delegation_chain
    if scopes is not None:
        payload["scopes"] = scopes


class Recorder:
    """Non-blocking MCAP recorder. Safe to call from the gateway safety path."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_queue: int = 10_000,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self.drops = 0
        self._clock_ns = clock_ns
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=max_queue)
        self._seq: dict[str, int] = dict.fromkeys(SCHEMAS, 0)
        self._out: dict[str, int] = dict.fromkeys(SCHEMAS, 0)
        self._lock = threading.Lock()
        self._closed = False
        self._file: IO[bytes] = open(path, "wb")  # noqa: SIM115
        self._writer = Writer(self._file)
        self._writer.start(profile="", library="thingwire")
        self._channels: dict[str, int] = {}
        for name, schema in SCHEMAS.items():
            sid = self._writer.register_schema(
                name=f"thingwire.{name}",
                encoding="jsonschema",
                data=json.dumps(schema).encode(),
            )
            self._channels[name] = self._writer.register_channel(
                topic=name, message_encoding="json", schema_id=sid
            )
        self._thread = threading.Thread(target=self._drain, name="tw-recorder", daemon=True)
        self._thread.start()

    def record_tool_call(
        self,
        device_id: str,
        action: str,
        params: dict[str, Any],
        *,
        approved: bool = False,
        agent_id: str | None = None,
        delegation_chain: list[str] | None = None,
        scopes: list[str] | None = None,
    ) -> int:
        """Record a tool call; returns its sequence number (use for decisions)."""
        with self._lock:
            seq = self._seq["tool_call"]
            self._seq["tool_call"] += 1
        payload: dict[str, Any] = {
            "seq": seq,
            "device_id": device_id,
            "action": action,
            "params": params,
            "approved": approved,
        }
        _put_identity(payload, agent_id, delegation_chain, scopes)
        self._enqueue("tool_call", payload)
        return seq

    def record_decision(
        self,
        call_seq: int,
        device_id: str,
        action: str,
        decision: Decision,
        *,
        agent_id: str | None = None,
        delegation_chain: list[str] | None = None,
        scopes: list[str] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "call_seq": call_seq,
            "device_id": device_id,
            "action": action,
            "allowed": decision.allowed,
            "code": decision.code,
            "message": decision.message,
        }
        _put_identity(payload, agent_id, delegation_chain, scopes)
        self._enqueue("decision", payload)

    def record_telemetry(self, device_id: str, readings: dict[str, Any]) -> None:
        self._enqueue("telemetry", {"device_id": device_id, "readings": readings})

    def record_td(self, device_id: str, td: dict[str, Any]) -> None:
        self._enqueue("td", {"device_id": device_id, "td": td})

    def _enqueue(self, channel: str, payload: dict[str, Any]) -> None:
        try:
            if self._closed:
                raise RuntimeError("recorder closed")
            data = json.dumps(payload, default=str).encode()
            self._queue.put_nowait((channel, self._clock_ns(), data))
        except Exception:
            self.drops += 1
            logger.warning("Recorder dropped %s record", channel, exc_info=True)

    def _drain(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            channel, ts, data = item
            try:
                seq = self._seq_out(channel)
                self._writer.add_message(
                    self._channels[channel], log_time=ts, data=data, publish_time=ts, sequence=seq
                )
            except Exception:
                self.drops += 1
                logger.warning("Recorder failed to write %s record", channel, exc_info=True)

    def _seq_out(self, channel: str) -> int:
        n = self._out[channel]
        self._out[channel] = n + 1
        return n

    def close(self) -> None:
        """Flush queued records and finish the MCAP file. Idempotent."""
        if self._closed:
            return
        self._closed = True
        try:
            self._queue.put(_STOP, timeout=5)
            self._thread.join(timeout=10)
            self._writer.finish()  # type: ignore[no-untyped-call]
        except Exception:
            logger.warning("Recorder close failed", exc_info=True)
        finally:
            self._file.close()


@dataclass
class Event:
    """One recorded message."""

    channel: str
    log_time: int
    data: dict[str, Any]


def read_recording(path: str | Path) -> list[Event]:
    """Read all messages from an MCAP file, ordered by log time."""
    with open(path, "rb") as f:
        reader = make_reader(f)
        events = [
            Event(ch.topic, msg.log_time, json.loads(msg.data))
            for _, ch, msg in reader.iter_messages(log_time_order=True)
        ]
    return events


class FakeClock:
    """Injected clock handed to SafetyLayer; advanced from recorded timestamps."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeDevice:
    """Virtual device that records the commands the safety layer let through."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, str, dict[str, Any]]] = []

    def send(self, device_id: str, action: str, params: dict[str, Any]) -> None:
        self.commands.append((device_id, action, params))


@dataclass
class ReplayResult:
    decisions: dict[int, dict[str, Any]] = field(default_factory=dict)
    diffs: list[dict[str, Any]] = field(default_factory=list)
    device: FakeDevice = field(default_factory=FakeDevice)


def replay(path: str | Path, safety_config: str | None = None) -> ReplayResult:
    """Re-run recorded tool calls through a fresh safety layer on a fake clock."""
    events = read_recording(path)
    recorded = {e.data["call_seq"]: e.data for e in events if e.channel == "decision"}
    result = ReplayResult()
    clock = FakeClock()
    t0 = events[0].log_time if events else 0
    safety = SafetyLayer(safety_config, clock=clock)
    for e in events:
        clock.now = 1000.0 + (e.log_time - t0) / 1e9
        if e.channel == "td":
            safety.register_device(e.data["device_id"], e.data["td"])
        elif e.channel == "tool_call":
            d = e.data
            decision = safety.evaluate(
                d["device_id"],
                d["action"],
                d["params"],
                approved=d.get("approved", False),
                agent_id=d.get("agent_id"),
                delegation_chain=d.get("delegation_chain"),
                scopes=d.get("scopes"),
            )
            result.decisions[d["seq"]] = {"allowed": decision.allowed, "code": decision.code}
            if decision.allowed:
                result.device.send(d["device_id"], d["action"], decision.params)
    for seq, new in sorted(result.decisions.items()):
        old = recorded.get(seq)
        if old is None or (old["allowed"], old["code"]) != (new["allowed"], new["code"]):
            result.diffs.append(
                {
                    "call_seq": seq,
                    "recorded": None
                    if old is None
                    else {"allowed": old["allowed"], "code": old["code"]},
                    "replayed": new,
                }
            )
    return result
