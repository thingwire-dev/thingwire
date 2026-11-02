"""Glue between SafetyLayer, the MQTT bridge and the audit log.

Inbound: `thingwire/{device}/estop` and `thingwire/estop` trip the latch (any
payload except the reset echo, and never a retained message, so a stale retained
trip cannot undo an authorized reset). Outbound: every state transition is
published retained on `safety_status` and written to the audit log, off the
event loop thread.
"""

import asyncio
import json
import logging
from typing import Protocol

from thingwire.safety import SafetyLayer
from thingwire.safety_pins import TdFlag
from thingwire.safety_status import SafetyTransition

logger = logging.getLogger(__name__)

RESET_PAYLOAD = json.dumps({"reset": True})
MAX_REASON_CHARS = 200


class _Publisher(Protocol):
    @property
    def prefix(self) -> str: ...

    def publish_nowait(
        self, topic: str, payload: str, retain: bool = ..., qos: int = ...
    ) -> bool: ...


class _Auditor(Protocol):
    async def record_transition(self, t: SafetyTransition) -> int: ...


def is_reset_payload(payload: bytes) -> bool:
    """True for the reset echo the gateway sends to a device after an authorized reset."""
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return False
    return isinstance(data, dict) and data.get("reset") is True


def status_topic(prefix: str, device_id: str | None) -> str:
    """Retained status topic for a device, or the global scope."""
    return f"{prefix}/safety_status" if device_id is None else f"{prefix}/{device_id}/safety_status"


class SafetyBridgeAdapter:
    """Wires a SafetyLayer to the bridge and audit log."""

    def __init__(
        self,
        layer: SafetyLayer,
        bridge: _Publisher,
        audit: _Auditor | None,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._layer = layer
        self._bridge = bridge
        self._audit = audit
        self._loop = loop
        self._tasks: set[asyncio.Task[None]] = set()
        layer.add_transition_listener(self.on_transition)
        layer.td_flags.add_listener(self.on_td_flag)

    def on_estop_message(self, device_id: str | None, payload: bytes, retained: bool) -> None:
        """Bridge callback for estop topics (runs on the MQTT thread)."""
        if retained or is_reset_payload(payload):
            return
        text = payload.decode("utf-8", errors="replace").strip()[:MAX_REASON_CHARS]
        reason = text or "estop topic message"
        self._layer.trip_estop(device_id, reason, source="mqtt")

    def on_transition(self, t: SafetyTransition) -> None:
        """Layer listener: hand off to the loop without blocking the caller."""
        try:
            self._loop.call_soon_threadsafe(self._spawn, t)
        except RuntimeError:
            logger.error("Event loop closed, transition %s not published", t.event)

    def on_td_flag(self, flag: TdFlag) -> None:
        """A published TD was looser than its pin: write an audit row (off the caller's thread)."""
        try:
            self._loop.call_soon_threadsafe(self._spawn_flag, flag)
        except RuntimeError:
            logger.error("Event loop closed, TD flag %s not audited", flag.event)

    def _spawn_flag(self, flag: TdFlag) -> None:
        task = self._loop.create_task(self._record_flag(flag))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _record_flag(self, flag: TdFlag) -> None:
        record = getattr(self._audit, "record", None)
        if record is None:
            return
        try:
            await record(
                flag.device_id,
                f"safety:{flag.event}",
                {},
                {"action": flag.action, "axis": flag.axis, **flag.detail},
                source="safety",
                decision="flag",
                reason=flag.event,
            )
        except Exception:
            logger.exception("Failed to audit TD flag %s", flag.event)

    def _spawn(self, t: SafetyTransition) -> None:
        task = self._loop.create_task(self._handle(t))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle(self, t: SafetyTransition) -> None:
        try:
            await asyncio.to_thread(self._publish, t)
        except Exception:
            logger.exception("Failed to publish safety status")
        if self._audit is not None:
            await self._audit.record_transition(t)

    def _publish(self, t: SafetyTransition) -> None:
        prefix = self._bridge.prefix
        self._bridge.publish_nowait(
            status_topic(prefix, t.device_id), json.dumps(t.payload()), retain=True
        )
        if t.event == "estop_reset" and t.device_id is not None:
            self._bridge.publish_nowait(f"{prefix}/{t.device_id}/estop", RESET_PAYLOAD)
