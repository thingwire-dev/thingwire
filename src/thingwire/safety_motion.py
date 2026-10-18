"""Motion safety primitives: structured decisions, range clamp/reject, slew limits.

Pure logic with an injectable clock so tests are deterministic. Limits come from
the WoT TD action input schema (`minimum`, `maximum`, and the ThingWire extension
`maxSlewDegPerSec`). Axes with a `unit` of deg (or an alias in ANGLE_UNITS) and
a range get a default 90 deg/s slew limit; unitless axes get none unless they set
`maxSlewDegPerSec` explicitly.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

DEFAULT_MAX_SLEW_DEG_PER_SEC = 90.0
ANGLE_UNITS = {"deg", "degree", "degrees", "°"}

Clock = Callable[[], float]


@dataclass(frozen=True)
class Decision:
    """Structured result of a safety evaluation. Recorded in the audit log."""

    allowed: bool
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def decision(self) -> str:
        """Audit-friendly verdict string."""
        return "allow" if self.allowed else "deny"


@dataclass(frozen=True)
class AxisLimit:
    """Limits for one numeric input axis."""

    minimum: float | None = None
    maximum: float | None = None
    max_slew_deg_per_sec: float | None = None


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def extract_limits(action: dict[str, Any]) -> dict[str, AxisLimit]:
    """Build per-axis limits from a TD action's input schema."""
    schema = action.get("input") or {}
    props: dict[str, Any] = schema.get("properties") or {"value": schema}
    limits: dict[str, AxisLimit] = {}
    for axis, spec in props.items():
        if not isinstance(spec, dict) or spec.get("type") not in ("number", "integer"):
            continue
        lo, hi = _num(spec.get("minimum")), _num(spec.get("maximum"))
        slew = _num(spec.get("maxSlewDegPerSec"))
        unit = spec.get("unit")
        angular = unit is not None and str(unit).lower() in ANGLE_UNITS
        if slew is None and angular and (lo is not None or hi is not None):
            slew = DEFAULT_MAX_SLEW_DEG_PER_SEC
        if lo is not None or hi is not None or slew is not None:
            limits[axis] = AxisLimit(lo, hi, slew)
    return limits


def _deny(code: str, message: str, **details: Any) -> Decision:
    return Decision(False, code, message, dict(details))


class MotionGuard:
    """Checks ranges and slew rate against last known commanded state."""

    def __init__(self, clock: Clock, mode: str = "reject") -> None:
        self._clock = clock
        self.mode = mode
        self._last: dict[tuple[str, str, str], tuple[float, float]] = {}

    def check(
        self,
        device_id: str,
        action: str,
        params: dict[str, Any],
        limits: dict[str, AxisLimit],
    ) -> Decision:
        """Validate params. Does not commit state; call commit() once all checks pass."""
        out = dict(params)
        clamped: dict[str, Any] = {}
        for axis, lim in limits.items():
            if axis not in out:
                continue
            value = _num(out[axis])
            if value is None or not math.isfinite(value):
                return _deny("INVALID_INPUT", f"'{axis}' must be a finite number", axis=axis)
            if (lim.minimum is not None and value < lim.minimum) or (
                lim.maximum is not None and value > lim.maximum
            ):
                bounds = {"minimum": lim.minimum, "maximum": lim.maximum}
                if self.mode != "clamp":
                    return _deny(
                        "OUT_OF_RANGE",
                        f"'{axis}'={value:g} is outside [{lim.minimum}, {lim.maximum}]",
                        axis=axis,
                        value=value,
                        **bounds,
                    )
                lo = lim.minimum if lim.minimum is not None else -math.inf
                hi = lim.maximum if lim.maximum is not None else math.inf
                new = min(max(value, lo), hi)
                clamped[axis] = {"requested": value, "applied": new}
                out[axis] = new
                value = new
            slew = self._slew(device_id, action, axis, value, lim)
            if slew is not None:
                return slew
        if clamped:
            return Decision(True, "RANGE_CLAMPED", "Input clamped to TD range", clamped, out)
        return Decision(True, "OK", "Within limits", {}, out)

    def _slew(
        self, device_id: str, action: str, axis: str, value: float, lim: AxisLimit
    ) -> Decision | None:
        prev = self._last.get((device_id, action, axis))
        if prev is None or lim.max_slew_deg_per_sec is None:
            return None
        elapsed = self._clock() - prev[1]
        delta = abs(value - prev[0])
        allowed = lim.max_slew_deg_per_sec * max(elapsed, 0.0)
        if delta > allowed + 1e-9:
            return _deny(
                "SLEW_EXCEEDED",
                f"'{axis}' moves {delta:g} in {max(elapsed, 0.0):.3f}s; "
                f"max {lim.max_slew_deg_per_sec:g} deg/s",
                axis=axis,
                delta=delta,
                elapsed=elapsed,
                max_slew_deg_per_sec=lim.max_slew_deg_per_sec,
            )
        return None

    def commit(self, device_id: str, action: str, params: dict[str, Any]) -> None:
        """Record accepted numeric values as the last known state."""
        now = self._clock()
        for axis, raw in params.items():
            value = _num(raw)
            if value is not None:
                self._last[(device_id, action, axis)] = (value, now)
