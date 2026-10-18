"""Shared helpers for e-stop tests."""

from pathlib import Path
from typing import Any

import yaml

from thingwire.safety import SafetyLayer
from thingwire.safety_status import SafetyTransition

SERVO_TD: dict[str, Any] = {
    "properties": {"angle": {"type": "number"}},
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


class FakeClock:
    """Manually advanced clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Ids:
    """Deterministic trace ids."""

    def __init__(self) -> None:
        self.n = 0

    def __call__(self) -> str:
        self.n += 1
        return f"trace-{self.n}"


def make_layer(
    tmp_path: Path,
    *,
    db: bool = True,
    threshold: int = 3,
    clock: FakeClock | None = None,
    resetters: list[str] | None = None,
) -> tuple[SafetyLayer, FakeClock, list[SafetyTransition]]:
    clock = clock or FakeClock()
    cfg = tmp_path / "safety.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "devices": {},
                "global": {
                    "deadman_switch_timeout_seconds": 100,
                    "default_rate_limit": {"max_calls": 1000, "window_seconds": 60},
                    "estop": {
                        "authorized_resetters": ["operator"] if resetters is None else resetters
                    },
                    "denial_flood": {
                        "threshold": threshold,
                        "window_seconds": 10,
                        "counted_codes": ["INVALID_INPUT", "OUT_OF_RANGE", "SLEW_EXCEEDED"],
                    },
                },
            }
        )
    )
    layer = SafetyLayer(
        str(cfg),
        clock,
        state_db_path=str(tmp_path / "state.db") if db else None,
        id_factory=Ids(),
    )
    events: list[SafetyTransition] = []
    layer.add_transition_listener(events.append)
    layer.register_device("servo", SERVO_TD)
    return layer, clock, events
