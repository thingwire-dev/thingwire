"""Scripted naive agent: obeys every instruction it finds, whatever channel it came from.

No model, no network. It scans text for `call tool(k=v, ...)` directives (optionally
`xN` to repeat) and one natural-language phrase, and executes them in order. A string
read result is scanned too, which is how sensor and tool-result injections land.
`@last` stands for the last number the agent read, optionally with `+n`, `-n` or `*n`.
"""

import re
from collections.abc import Callable
from typing import Any

from inj_arms import Arm, Outcome
from inj_model import OPERATOR, Step

CALL_RE = re.compile(r"\bcall\s+([a-z_][a-z0-9_]*)\(([^)]*)\)(?:\s*x(\d+))?", re.IGNORECASE)
SWEEP_RE = re.compile(r"\bsweep the head at max(?:imum)? speed\b", re.IGNORECASE)
LAST_RE = re.compile(r"@last(?:\s*([+\-*])\s*(\d+(?:\.\d+)?))?")
INT_RE = re.compile(r"-?\d+")
MAX_DEPTH = 2
SWEEP_MAX = 120


def _value(raw: str, last: float | None) -> Any:
    text = raw.strip().strip("\"'")
    m = LAST_RE.fullmatch(text)
    if m:
        base = last if last is not None else 0.0
        op, num = m.group(1), float(m.group(2) or 0)
        return {"+": base + num, "-": base - num, "*": base * num}.get(op or "", base)
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    if INT_RE.fullmatch(text):
        return int(text)
    try:
        return float(text)
    except ValueError:
        return text


def parse_args(raw: str, last: float | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for part in raw.split(","):
        if "=" in part:
            key, val = part.split("=", 1)
            out[key.strip()] = _value(val, last)
    return out


class NaiveAgent:
    """Executes directives against one arm and records every call it makes."""

    def __init__(self, arm: Arm, tick: Callable[[], None]) -> None:
        self._arm = arm
        self._tick = tick  # callable(): advances the world after each call
        self.steps: list[Step] = []
        self.last: float | None = None

    async def ingest(self, channel: str, text: str, depth: int = 0) -> None:
        """Read `text` as if it came from `channel` and obey what it says."""
        found: list[tuple[int, str, str, int]] = []
        for m in CALL_RE.finditer(text):
            found.append((m.start(), m.group(1), m.group(2), int(m.group(3) or 1)))
        for m in SWEEP_RE.finditer(text):
            found.append((m.start(), "do_sweep", f"speed_deg_s={SWEEP_MAX}", 1))
        for _, tool, raw, times in sorted(found):
            for _ in range(times):
                await self._run(channel, tool, parse_args(raw, self.last), depth)

    async def _run(self, channel: str, tool: str, params: dict[str, Any], depth: int) -> None:
        out: Outcome = await self._arm.invoke(tool, params, channel)
        self._tick()
        self.steps.append(Step(channel, tool, out.ok, out.code, out.delivered))
        if out.kind != "read" or not out.ok:
            return
        if isinstance(out.value, int | float) and not isinstance(out.value, bool):
            self.last = float(out.value)
        elif isinstance(out.value, str) and depth < MAX_DEPTH:
            await self.ingest("sensor_reading", out.value, depth + 1)


def operator_steps(steps: list[Step]) -> list[Step]:
    return [s for s in steps if s.channel == OPERATOR]
