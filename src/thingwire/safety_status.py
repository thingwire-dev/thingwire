"""Safety status transitions and the retained safety_status payload."""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from thingwire.safety_codes import SafetyState


@dataclass(frozen=True)
class SafetyTransition:
    """One state transition, destined for MQTT and the audit log."""

    device_id: str | None  # None means the global scope
    state: SafetyState
    drop_reason: str | None
    last_decision_code: str
    trace_id: str
    timestamp: str
    event: str
    actor: str | None = None
    detail: str = ""

    def payload(self) -> dict[str, Any]:
        """Retained MQTT payload."""
        return {
            "state": self.state.value,
            "drop_reason": self.drop_reason,
            "last_decision_code": self.last_decision_code,
            "trace_id": self.trace_id,
            "timestamp": self.timestamp,
        }


class StatusTracker:
    """Remembers per-scope state and emits a transition only when it changes."""

    def __init__(self, wall_clock: Callable[[], datetime] | None = None) -> None:
        self._wall = wall_clock or (lambda: datetime.now(UTC))
        self._state: dict[str | None, tuple[SafetyState, str | None]] = {}
        self._lock = threading.Lock()

    def update(
        self,
        device_id: str | None,
        state: SafetyState,
        drop_reason: str | None,
        code: str,
        trace_id: str,
        event: str,
        actor: str | None = None,
        detail: str = "",
    ) -> SafetyTransition | None:
        """Return a transition if (state, drop_reason) changed, else None."""
        with self._lock:
            if self._state.get(device_id) == (state, drop_reason):
                return None
            self._state[device_id] = (state, drop_reason)
        stamp = self._wall().isoformat().replace("+00:00", "Z")
        return SafetyTransition(
            device_id, state, drop_reason, code, trace_id, stamp, event, actor, detail
        )
