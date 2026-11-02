"""Display behavior for the virtual desk bot: show_text and blink (contracts 1.6, 2.3).

Limits are read from firmware/device-deskbot.yaml, the same file the firmware TD is generated
from, so the simulator and the device cannot drift. Errors are the fixed 1.6 strings and never
echo the text. Text is opaque data: it is stored and counted down, never parsed.
"""

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

CONFIG_PATH = Path(__file__).resolve().parent.parent / "firmware" / "device-deskbot.yaml"
BLINK_CYCLE_S = 0.3
SLOW_FACTOR = 2.0
SPEEDS = ("normal", "slow")


def load_display_limits(path: Path = CONFIG_PATH) -> dict[str, int]:
    """Text and blink limits from the device config."""
    cfg = yaml.safe_load(path.read_text())
    disp = cfg["display"]
    return {
        "text_max_chars": int(disp["text_max_chars"]),
        "text_default_ms": int(disp["text_default_ms"]),
        "text_max_ms": int(disp["text_max_ms"]),
        "blink_max_times": int(cfg["blink"]["max_times"]),
    }


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class DisplayMixin:
    """Text overlay and blink state. Timers run on the simulation clock (step dt)."""

    expression: str
    display_limits: dict[str, int]
    text: str | None = None
    text_left_s: float = 0.0
    blink_left_s: float = 0.0

    def init_display(self) -> None:
        """Load limits and clear the display state."""
        self.display_limits = load_display_limits()
        self.clear_display()

    def clear_display(self) -> None:
        """Drop visible text and any blink; the stored expression face shows again."""
        self.text = None
        self.text_left_s = 0.0
        self.blink_left_s = 0.0

    def step_display(self, dt: float) -> None:
        """Count timers down; an expired text restores the expression face."""
        if self.text is not None:
            self.text_left_s -= dt
            if self.text_left_s <= 0:
                self.text, self.text_left_s = None, 0.0
        self.blink_left_s = max(0.0, self.blink_left_s - dt)

    def trip_estop(self) -> None:
        """E-stop clears the display, then parks the head (EstopMixin)."""
        self.clear_display()
        super().trip_estop()  # type: ignore[misc]

    def show_text(self, args: dict[str, Any]) -> None:
        """Validate and show one line of printable ASCII, replacing any visible text."""
        lim = self.display_limits
        text = args.get("text")
        if not isinstance(text, str) or any(not " " <= c <= "~" for c in text):
            raise ValueError("text charset")
        if not text.strip(" "):
            raise ValueError("text blank")
        if len(text) > lim["text_max_chars"]:
            raise ValueError("text too long")
        duration: Any = args.get("duration_ms", lim["text_default_ms"])
        if not _is_int(duration) or not 1 <= duration <= lim["text_max_ms"]:
            raise ValueError("duration out of range")
        self.blink_left_s = 0.0
        self.text, self.text_left_s = text, duration / 1000.0

    def blink(self, args: dict[str, Any]) -> None:
        """Validate and start a blink; refused while text covers the eyes."""
        times: Any = args.get("times")
        speed: Any = args.get("speed", "normal")
        if not _is_int(times) or not 1 <= times <= self.display_limits["blink_max_times"]:
            raise ValueError("times out of range")
        if speed not in SPEEDS:
            raise ValueError("unknown speed")
        if self.text is not None:
            raise ValueError("display busy")
        factor = SLOW_FACTOR if speed == "slow" else 1.0
        self.blink_left_s = times * BLINK_CYCLE_S * factor
