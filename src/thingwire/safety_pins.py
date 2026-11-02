"""Operator pinned policy for device TDs (contracts 5.4).

A TD is a claim by the device. Pins are what the operator enforces: the stricter of the pin and
the TD always wins, and any way the TD is looser is returned so it can be flagged and audited.
Pure logic, no I/O.
"""

import logging
import math
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from thingwire.safety_motion import AxisLimit, same_json

logger = logging.getLogger(__name__)

EVENT_WIDENED = "td_limit_widened"
EVENT_APPROVAL_LOWERED = "td_approval_lowered"
EVENT_UNPINNED_ACTION = "td_action_unpinned"
UNPINNED_MODES = frozenset({"auto", "deny", "trust"})
_AXIS_KEYS = frozenset({"minimum", "maximum", "max_slew_deg_per_sec", "enum"})


@dataclass(frozen=True)
class PinnedAxis:
    """Operator limits for one action input axis."""

    minimum: float | None = None
    maximum: float | None = None
    max_slew_deg_per_sec: float | None = None
    enum: tuple[Any, ...] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "minimum": self.minimum,
            "maximum": self.maximum,
            "slew": self.max_slew_deg_per_sec,
            "enum": list(self.enum) if self.enum is not None else None,
        }


@dataclass(frozen=True)
class PinnedAction:
    requires_approval: bool = False
    inputs: Mapping[str, PinnedAxis] = field(default_factory=dict)


@dataclass(frozen=True)
class TdFlag:
    """A way a published TD is looser than its pin. The pinned value is what is enforced."""

    event: str
    device_id: str
    action: str
    axis: str | None
    detail: dict[str, Any]


class FlagBook:
    """History of TD flags; a new listener is replayed everything that already happened."""

    def __init__(self) -> None:
        self.history: list[TdFlag] = []
        self._listeners: list[Callable[[TdFlag], None]] = []
        self._lock = threading.Lock()

    def emit(self, flags: list[TdFlag]) -> None:
        for flag in flags:
            logger.warning(
                "TD flag %s on %s/%s axis=%s: %s",
                flag.event, flag.device_id, flag.action, flag.axis, flag.detail,
            )  # fmt: skip
            with self._lock:
                self.history.append(flag)
                listeners = list(self._listeners)
            for listener in listeners:
                self._notify(listener, flag)

    def add_listener(self, listener: Callable[[TdFlag], None]) -> None:
        with self._lock:
            self._listeners.append(listener)
            past = list(self.history)
        for flag in past:
            self._notify(listener, flag)

    @staticmethod
    def _notify(listener: Callable[[TdFlag], None], flag: TdFlag) -> None:
        try:
            listener(flag)
        except Exception:
            logger.exception("TD flag listener failed")


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{where} must be a finite number")
    return float(value)


def _axis(spec: Any, where: str) -> PinnedAxis:
    if not isinstance(spec, Mapping) or set(spec) - _AXIS_KEYS:
        raise ValueError(f"{where} must be a mapping with keys {sorted(_AXIS_KEYS)}")
    enum = spec.get("enum")
    if enum is not None and (not isinstance(enum, list) or not enum):
        raise ValueError(f"{where}.enum must be a non-empty list")
    num = {k: _number(spec[k], f"{where}.{k}") for k in _AXIS_KEYS - {"enum"} if k in spec}
    return PinnedAxis(
        num.get("minimum"),
        num.get("maximum"),
        num.get("max_slew_deg_per_sec"),
        tuple(enum) if enum is not None else None,
    )


def _action(spec: Any, where: str) -> PinnedAction:
    if not isinstance(spec, Mapping):
        raise ValueError(f"{where} must be a mapping")
    approval = spec.get("requires_approval", False)
    inputs = spec.get("inputs") or {}
    if not isinstance(approval, bool) or not isinstance(inputs, Mapping):
        raise ValueError(f"{where}: requires_approval is a boolean, inputs a mapping")
    return PinnedAction(
        approval, {str(k): _axis(v, f"{where}.inputs.{k}") for k, v in inputs.items()}
    )


@dataclass(frozen=True)
class TdTrust:
    """The parsed `td_trust:` section."""

    unpinned: str = "auto"
    devices: Mapping[str, Mapping[str, PinnedAction]] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "TdTrust":
        """Parse the full safety config. Raises ValueError on anything malformed."""
        section = config.get("td_trust")
        if section is None:
            return cls()
        if not isinstance(section, Mapping):
            raise ValueError("td_trust must be a mapping")
        unpinned = section.get("unpinned", "auto")
        if unpinned not in UNPINNED_MODES:
            raise ValueError(f"td_trust.unpinned must be one of {sorted(UNPINNED_MODES)}")
        raw = section.get("devices")
        raw = {} if raw is None else raw
        if not isinstance(raw, Mapping):
            raise ValueError("td_trust.devices must be a mapping")
        devices: dict[str, dict[str, PinnedAction]] = {}
        for dev, body in raw.items():
            actions = body.get("actions") if isinstance(body, Mapping) else None
            if not isinstance(actions, Mapping):
                raise ValueError(f"td_trust.devices.{dev}.actions must be a mapping")
            devices[str(dev)] = {
                str(n): _action(a, f"td_trust.devices.{dev}.actions.{n}")
                for n, a in actions.items()
            }
        return cls(unpinned, devices)

    def unpinned_mode(self) -> str:
        """`deny` or `trust`: `auto` denies as soon as any device is pinned."""
        if self.unpinned == "auto":
            return "deny" if self.devices else "trust"
        return self.unpinned


def td_view(lim: AxisLimit | None) -> dict[str, Any]:
    if lim is None:
        return {"minimum": None, "maximum": None, "slew": None, "enum": None}
    return {
        "minimum": lim.minimum,
        "maximum": lim.maximum,
        "slew": lim.max_slew_deg_per_sec,
        "enum": list(lim.enum) if lim.enum is not None else None,
    }


def merge_axis(td: AxisLimit | None, pin: PinnedAxis) -> tuple[AxisLimit, list[str]]:
    """Stricter of TD and pin per field, plus the fields where the TD is looser than the pin."""
    t = td or AxisLimit()
    wide: list[str] = []

    lo = _tighter(t.minimum, pin.minimum, max)
    hi = _tighter(t.maximum, pin.maximum, min)
    slew = _tighter(t.max_slew_deg_per_sec, pin.max_slew_deg_per_sec, min)
    if pin.minimum is not None and (t.minimum is None or t.minimum < pin.minimum):
        wide.append("minimum")
    if pin.maximum is not None and (t.maximum is None or t.maximum > pin.maximum):
        wide.append("maximum")
    if pin.max_slew_deg_per_sec is not None and (
        t.max_slew_deg_per_sec is None or t.max_slew_deg_per_sec > pin.max_slew_deg_per_sec
    ):
        wide.append("slew")
    enum = t.enum
    if pin.enum is not None:
        enum = tuple(p for p in pin.enum if t.enum is None or any(same_json(p, e) for e in t.enum))
        if t.enum is None or any(not any(same_json(e, p) for p in pin.enum) for e in t.enum):
            wide.append("enum")
    return AxisLimit(lo, hi, slew, enum, t.slew_key), wide


def _tighter(a: float | None, b: float | None, strict: Callable[..., float]) -> float | None:
    if a is None or b is None:
        return b if a is None else a
    return strict(a, b)
