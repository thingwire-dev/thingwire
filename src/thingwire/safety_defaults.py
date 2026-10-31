"""Built-in safety configuration, used when no YAML config file is found."""

from typing import Any


def default_config() -> dict[str, Any]:
    """A fresh copy of the default config (callers mutate it)."""
    return {
        "devices": {},
        "global": {
            "require_confirmation_for_dangerous": True,
            "deadman_switch_timeout_seconds": 300,
            "audit_log_retention_days": 30,
            "default_rate_limit": {"max_calls": 10, "window_seconds": 60},
            "estop": {"authorized_resetters": ["operator"]},
            "denial_flood": {
                "threshold": 20,
                "window_seconds": 60,
                "counted_codes": [
                    "INVALID_INPUT",
                    "OUT_OF_RANGE",
                    "SLEW_EXCEEDED",
                    "ACTION_NOT_ALLOWED",
                ],
            },
        },
    }
