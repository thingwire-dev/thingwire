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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from mcap.reader import make_reader
from mcap.writer import Writer

from thingwire.safety import SafetyError, SafetyLayer

logger = logging.getLogger(__name__)

_STR = {"type": "string"}
_OBJ = {"type": "object"}
_NUM = {"type": "number"}
SCHEMAS: dict[str, dict[str, Any]] = {
    "tool_call": {
        "type": "object",
        "properties": {"seq": _NUM, "device_id": _STR, "action": _STR, "params": _OBJ},
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
        },
    },
    "telemetry": {
        "type": "object",
        "properties": {"device_id": _STR, "readings": _OBJ},
    },
    "td": {"type": "object", "properties": {"device_id": _STR, "td": _OBJ}},
}
_STOP = object()


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

    def record_tool_call(self, device_id: str, action: str, params: dict[str, Any]) -> int:
        """Record a tool call; returns its sequence number (use for decisions)."""
        with self._lock:
            seq = self._seq["tool_call"]
            self._seq["tool_call"] += 1
        self._enqueue(
            "tool_call", {"seq": seq, "device_id": device_id, "action": action, "params": params}
        )
        return seq

    def record_decision(
        self,
        call_seq: int,
        device_id: str,
        action: str,
        allowed: bool,
        code: str = "",
        message: str = "",
    ) -> None:
        self._enqueue(
            "decision",
            {
                "call_seq": call_seq,
                "device_id": device_id,
                "action": action,
                "allowed": allowed,
                "code": code,
                "message": message,
            },
        )

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
    """Injected clock replacing time.monotonic inside the safety layer."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


class FakeDevice:
    """Virtual device that records the commands the safety layer let through."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, str, dict[str, Any]]] = []

    def send(self, device_id: str, action: str, params: dict[str, Any]) -> None:
        self.commands.append((device_id, action, params))


@contextmanager
def _patched_clock(clock: FakeClock) -> Iterator[None]:
    """Swap safety.time for a shim exposing the fake monotonic clock."""
    import thingwire.safety as safety_mod

    class _Shim:
        monotonic = staticmethod(clock.monotonic)

    ns: dict[str, Any] = vars(safety_mod)
    original = ns["time"]
    ns["time"] = _Shim
    try:
        yield
    finally:
        ns["time"] = original


def decide(safety: SafetyLayer, device_id: str, action: str) -> tuple[bool, str, str]:
    """Adapter over today's safety.py (raises SafetyError) -> (allowed, code, message).

    Swap this body once W2 returns structured decisions with reason codes.
    """
    try:
        safety.check_permission(device_id, action)
        safety.check_rate_limit(device_id, action)
        safety.check_deadman_switch(device_id)
    except SafetyError as e:
        return False, e.code, e.message
    return True, "", ""


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
    safety = SafetyLayer(safety_config)
    with _patched_clock(clock):
        for e in events:
            clock.now = 1000.0 + (e.log_time - t0) / 1e9
            if e.channel == "td":
                safety.register_device(e.data["device_id"], e.data["td"])
            elif e.channel == "telemetry":
                safety.update_heartbeat_from_telemetry(e.data["device_id"], clock.now)
            elif e.channel == "tool_call":
                d = e.data
                allowed, code, _ = decide(safety, d["device_id"], d["action"])
                result.decisions[d["seq"]] = {"allowed": allowed, "code": code}
                if allowed:
                    result.device.send(d["device_id"], d["action"], d["params"])
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
