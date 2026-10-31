"""Simulated world: injectable clock, the device adapter, and the shared tool registry.

The adapter wraps the repo's virtual desk bot, so a delivered command really goes through
the same device-side code the demo uses. Nothing here touches the network or wall time.
"""

import importlib.util
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from inj_model import Delivery
from inj_tds import DESK, RELAY, ROOT


def load_bot_module() -> Any:
    """Load scripts/virtual_desk_bot.py once (it is a script, not a package)."""
    name = "virtual_desk_bot"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "virtual_desk_bot.py")
    if spec is None or spec.loader is None:
        raise ImportError("cannot load virtual_desk_bot")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class SimClock:
    """Monotonic clock the safety layer, taint ledger and harness all share."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class IdSource:
    """Seeded trace and action ids so two runs produce identical records."""

    def __init__(self, seed: int) -> None:
        import random

        self._rng = random.Random(seed)

    def __call__(self) -> str:
        return f"{self._rng.getrandbits(64):016x}"


@dataclass
class DeviceAdapter:
    """Stands in for the MQTT bridge: holds readings, applies commands, records deliveries."""

    clock: SimClock
    bot: Any = field(default=None)
    readings: dict[tuple[str, str], Any] = field(default_factory=dict)
    sent: list[Delivery] = field(default_factory=list)
    origin: str = "operator"

    def __post_init__(self) -> None:
        mod = load_bot_module()
        self.bot = mod.DeskBot(mod.load_scene())

    def is_device_online(self, device_id: str) -> bool:
        return True

    def get_latest_reading(self, device_id: str, property_name: str) -> dict[str, Any]:
        value = self.readings.get((device_id, property_name), 0.0)
        return {"device_id": device_id, "property": property_name, "value": value, "unit": None}

    async def send_command(self, device_id: str, target: str, command: str, value: Any) -> str:
        action_id = f"a{len(self.sent)}"
        cmd = {"action_id": action_id, "target": target, "command": command, "value": value}
        if device_id == DESK:
            executed = self.bot.apply_command(cmd).get("status") == "ok"
        else:
            executed = device_id == RELAY and command == "set_relay"
        payload = dict(value) if isinstance(value, dict) else {}
        self.sent.append(Delivery(device_id, command, payload, self.origin, executed, self.clock()))
        return action_id

    def trip_device_estop(self) -> None:
        """The device-side latch, which a real MQTT e-stop also reaches."""
        self.bot.trip_estop()


Heartbeat = Callable[[], None]
