"""E-stop behavior for the virtual desk bot: halt, park, ignore commands until reset.

Any message on the estop topics trips the stop. Only the JSON payload
{"reset": true} (sent by the gateway after an authorized reset) clears it.
"""

import json
from typing import Any

PARK_POSE = (0.0, 0.0)
ESTOP_CODE = "ESTOP_LATCHED"


def is_reset_payload(payload: bytes) -> bool:
    """True only for an explicit reset message."""
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return False
    return isinstance(data, dict) and data.get("reset") is True


class EstopMixin:
    """Adds an e-stop latch to a DeskBot-like object (pan/tilt target state)."""

    estopped: bool = False
    _target: tuple[float, float]
    _waypoints: list[tuple[float, float]]
    _sweep_speed: float | None

    def trip_estop(self) -> None:
        """Stop motion immediately and slew (within limits) to the park pose."""
        self.estopped = True
        self._sweep_speed = None
        self._waypoints.clear()
        self._target = PARK_POSE

    def reset_estop(self) -> None:
        """Clear the latch. The bot stays parked until commanded."""
        self.estopped = False

    def handle_estop_payload(self, payload: bytes) -> str:
        """Apply an estop-topic message. Returns 'reset' or 'tripped'."""
        if is_reset_payload(payload):
            self.reset_estop()
            return "reset"
        self.trip_estop()
        return "tripped"

    @staticmethod
    def estop_ack(action_id: str, now: str) -> dict[str, Any]:
        """Ack for a command refused while e-stopped."""
        return {
            "action_id": action_id,
            "status": "error",
            "error": ESTOP_CODE,
            "timestamp": now,
        }
