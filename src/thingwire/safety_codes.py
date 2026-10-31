"""Decision codes, safety states and the SafetyError exception."""

from enum import StrEnum
from typing import Any


class DecisionCode(StrEnum):
    """Every code a safety Decision can carry."""

    OK = "OK"
    RANGE_CLAMPED = "RANGE_CLAMPED"
    INVALID_INPUT = "INVALID_INPUT"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    SLEW_EXCEEDED = "SLEW_EXCEEDED"
    DEVICE_NOT_REGISTERED = "DEVICE_NOT_REGISTERED"
    ACTION_NOT_ALLOWED = "ACTION_NOT_ALLOWED"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    DEADMAN_SWITCH = "DEADMAN_SWITCH"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    SAFETY_CHECK_ERROR = "SAFETY_CHECK_ERROR"
    ESTOP_LATCHED = "ESTOP_LATCHED"
    DENIAL_FLOOD = "DENIAL_FLOOD"
    RESET_NOT_AUTHORIZED = "RESET_NOT_AUTHORIZED"
    ESTOP_STATE_UNAVAILABLE = "ESTOP_STATE_UNAVAILABLE"
    STALE_COMMAND = "STALE_COMMAND"
    REPLAYED_COMMAND = "REPLAYED_COMMAND"
    TD_NOT_PINNED = "TD_NOT_PINNED"


class SafetyState(StrEnum):
    """Published per-device safety state."""

    OK = "ok"
    DEGRADED = "degraded"
    ESTOP = "estop"


class SafetyError(Exception):
    """Raised when a safety check fails."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)
