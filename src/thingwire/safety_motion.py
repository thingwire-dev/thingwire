"""Motion safety primitives: structured decisions, range clamp/reject, slew limits.

Pure logic with an injectable clock so tests are deterministic. Limits come from
the WoT TD action input schema (`minimum`, `maximum`, `enum` and the ThingWire extension
`maxSlewDegPerSec`). `maxSlewDegPerSec` may also sit on the TD property an axis drives
(contracts 2.1); the smallest declared value wins. Axes with a `unit` of deg (or an alias in
ANGLE_UNITS) and a range get a default 90 deg/s slew limit when nothing else declares one.
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
    """Limits for one input axis. `slew_key` names the driven property that owns the history."""

    minimum: float | None = None
    maximum: float | None = None
    max_slew_deg_per_sec: float | None = None
    enum: tuple[Any, ...] | None = None
    slew_key: str | None = None


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _driven(axis: str, properties: dict[str, Any]) -> list[str]:
    """Numeric properties an action axis drives: exact name, else names ending in `_<axis>`."""
    numeric = [
        name
        for name, spec in properties.items()
        if isinstance(spec, dict) and spec.get("type") in ("number", "integer")
    ]
    if axis in numeric:
        return [axis]
    return sorted(name for name in numeric if name.endswith(f"_{axis}"))


def _property_slew(axis: str, properties: dict[str, Any]) -> tuple[float | None, str | None]:
    """Strictest slew declared on the properties `axis` drives, and the history key."""
    driven = _driven(axis, properties)
    declared = [(_num(properties[n].get("maxSlewDegPerSec")), n) for n in driven]
    slews = [(v, n) for v, n in declared if v is not None]
    key = driven[0] if len(driven) == 1 else (",".join(driven) or None)
    return (min(v for v, _ in slews) if slews else None), key


def _enum_of(spec: dict[str, Any]) -> tuple[Any, ...] | None:
    values = spec.get("enum")
    return tuple(values) if isinstance(values, list) else None


def extract_limits(
    action: dict[str, Any], properties: dict[str, Any] | None = None
) -> dict[str, AxisLimit]:
    """Build per-axis limits from a TD action's input schema and, for slew, TD properties."""
    schema = action.get("input") or {}
    props: dict[str, Any] = schema.get("properties") or {"value": schema}
    limits: dict[str, AxisLimit] = {}
    for axis, spec in props.items():
        if not isinstance(spec, dict):
            continue
        enum = _enum_of(spec)
        if spec.get("type") not in ("number", "integer"):
            if enum is not None:
                limits[axis] = AxisLimit(enum=enum)
            continue
        lo, hi = _num(spec.get("minimum")), _num(spec.get("maximum"))
        declared = [v for v in (_num(spec.get("maxSlewDegPerSec")),) if v is not None]
        prop_slew, key = _property_slew(axis, properties or {})
        if prop_slew is not None:
            declared.append(prop_slew)
        slew = min(declared) if declared else None
        unit = spec.get("unit")
        angular = unit is not None and str(unit).lower() in ANGLE_UNITS
        if slew is None and angular and (lo is not None or hi is not None):
            slew = DEFAULT_MAX_SLEW_DEG_PER_SEC
        if lo is not None or hi is not None or slew is not None or enum is not None:
            limits[axis] = AxisLimit(lo, hi, slew, enum, key if slew is not None else None)
    return limits


def _same(a: Any, b: Any) -> bool:
    """JSON equality: booleans only equal booleans, numbers equal numbers, no coercion."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return a == b
    return type(a) is type(b) and a == b


def _history_key(
    device_id: str, action: str, axis: str, lim: AxisLimit | None
) -> tuple[str, str, str]:
    """Slew history is shared per driven property, else kept per action and axis."""
    if lim is not None and lim.slew_key:
        return (device_id, "property", lim.slew_key)
    return (device_id, action, axis)


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
            if lim.enum is not None and not any(_same(out[axis], e) for e in lim.enum):
                return _deny(
                    "INVALID_INPUT",
                    f"'{axis}' is not one of the allowed values",
                    axis=axis,
                    reason="not_in_enum",
                    allowed=list(lim.enum),
                )
            if lim.enum is not None and lim.minimum is None and lim.maximum is None:
                continue  # a non-numeric enum axis has nothing else to check
            value = _num(out[axis])
            if value is None or not math.isfinite(value):
                return _deny("INVALID_INPUT", f"'{axis}' must be a finite number", axis=axis)
            if (lim.minimum is not None and value < lim.minimum) or (
                lim.maximum is not None and value > lim.maximum
            ):
                bounds = {"minimum": lim.minimum, "maximum": lim.maximum}
                if self.mode != "clamp" or lim.enum is not None:  # an enum is never clamped
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
        prev = self._last.get(_history_key(device_id, action, axis, lim))
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

    def commit(
        self,
        device_id: str,
        action: str,
        params: dict[str, Any],
        limits: dict[str, AxisLimit] | None = None,
    ) -> None:
        """Record accepted numeric values as the last known state."""
        now = self._clock()
        for axis, raw in params.items():
            value = _num(raw)
            if value is not None:
                key = _history_key(device_id, action, axis, (limits or {}).get(axis))
                self._last[key] = (value, now)
