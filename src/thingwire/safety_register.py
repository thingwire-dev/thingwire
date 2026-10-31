"""Derive a device's safety rules from its published TD and the operator's pins (contracts 5.4)."""

import logging
from dataclasses import dataclass, field
from typing import Any

from thingwire.safety_motion import AxisLimit, extract_limits
from thingwire.safety_pins import (
    EVENT_APPROVAL_LOWERED,
    EVENT_UNPINNED_ACTION,
    EVENT_WIDENED,
    PinnedAction,
    TdFlag,
    TdTrust,
    merge_axis,
    td_view,
)

logger = logging.getLogger(__name__)


@dataclass
class DeviceRules:
    """What the safety layer enforces for one device."""

    allowed: list[str] = field(default_factory=list)
    dangerous: list[str] = field(default_factory=list)
    limits: dict[str, dict[str, AxisLimit]] = field(default_factory=dict)
    flags: list[TdFlag] = field(default_factory=list)
    unpinned_denied: bool = False


def _merged_limits(
    device_id: str,
    name: str,
    td_limits: dict[str, AxisLimit],
    pin: PinnedAction,
    rules: DeviceRules,
) -> dict[str, AxisLimit]:
    limits = dict(td_limits)
    for axis, pinned in pin.inputs.items():
        merged, wide = merge_axis(td_limits.get(axis), pinned)
        limits[axis] = merged
        if wide:
            detail = {
                "fields": wide,
                "td": td_view(td_limits.get(axis)),
                "pinned": pinned.as_dict(),
            }
            rules.flags.append(TdFlag(EVENT_WIDENED, device_id, f"do_{name}", axis, detail))
    return limits


def derive_rules(device_id: str, td: dict[str, Any], trust: TdTrust) -> DeviceRules:
    """Allowlist, approval set and limits for a device. The stricter of pin and TD wins."""
    props = td.get("properties") or {}
    rules = DeviceRules(allowed=[f"read_{name}" for name in props])
    pins = trust.devices.get(device_id)
    if pins is None and trust.unpinned_mode() == "deny":
        rules.unpinned_denied = True
        logger.warning("Device %s has no pin; actuation denied (td_trust.unpinned)", device_id)
        return rules
    if pins is None:
        logger.warning("Device %s is not pinned; using the limits its TD publishes", device_id)
    for name, action in (td.get("actions") or {}).items():
        pin = pins.get(name) if pins is not None else None
        if pins is not None and pin is None:
            detail: dict[str, Any] = {"reason": "action is not in the pin"}
            rules.flags.append(TdFlag(EVENT_UNPINNED_ACTION, device_id, f"do_{name}", None, detail))
            continue
        safe = bool(action.get("safe", True))
        td_limits = extract_limits(action, props)
        rules.allowed.append(f"do_{name}")
        rules.limits[f"do_{name}"] = (
            _merged_limits(device_id, name, td_limits, pin, rules) if pin else td_limits
        )
        if not safe or (pin is not None and pin.requires_approval):
            rules.dangerous.append(f"do_{name}")
        if pin is not None and pin.requires_approval and safe:
            lowered = {"td_safe": True, "pinned_requires_approval": True}
            rules.flags.append(
                TdFlag(EVENT_APPROVAL_LOWERED, device_id, f"do_{name}", None, lowered)
            )
    return rules
