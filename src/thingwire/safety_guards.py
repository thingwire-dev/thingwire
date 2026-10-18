"""Composes e-stop latch, denial flood guard and status tracking for SafetyLayer."""

import logging
import threading
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from thingwire.safety_codes import DecisionCode, SafetyError, SafetyState
from thingwire.safety_estop import GLOBAL_SCOPE, EstopLatch, LatchRecord
from thingwire.safety_flood import DenialFloodGuard
from thingwire.safety_status import SafetyTransition, StatusTracker

logger = logging.getLogger(__name__)

Listener = Callable[[SafetyTransition], None]


def _new_trace() -> str:
    return uuid.uuid4().hex


class SafetyGuards:
    """Latch, flood guard and status transitions behind one lock."""

    def __init__(
        self,
        global_cfg: dict[str, Any],
        clock: Callable[[], float],
        db_path: str | None = None,
        wall_clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] = _new_trace,
    ) -> None:
        estop_cfg = global_cfg.get("estop") or {}
        flood_cfg = global_cfg.get("denial_flood") or {}
        kwargs: dict[str, Any] = {"wall_clock": wall_clock} if wall_clock else {}
        self.latch = EstopLatch(db_path, estop_cfg.get("authorized_resetters") or [], **kwargs)
        self.flood = DenialFloodGuard(
            int(flood_cfg.get("threshold", 0)),
            float(flood_cfg.get("window_seconds", 60)),
            flood_cfg.get("counted_codes") or [],
            clock,
        )
        self.tracker = StatusTracker(wall_clock)
        self._new_trace = id_factory
        self._listeners: list[Listener] = []
        self._devices: set[str] = set()
        self._lock = threading.RLock()

    def new_trace(self) -> str:
        return self._new_trace()

    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def _emit(self, transition: SafetyTransition | None) -> None:
        if transition is None:
            return
        for listener in list(self._listeners):
            try:
                listener(transition)
            except Exception:
                logger.exception("Safety transition listener failed")

    def _state_for(self, scope: str | None) -> tuple[SafetyState, str | None]:
        key = GLOBAL_SCOPE if scope is None else scope
        if self.latch.latched_for(key):
            return SafetyState.ESTOP, DecisionCode.ESTOP_LATCHED
        if scope is not None and self.flood.active(scope):
            return SafetyState.DEGRADED, DecisionCode.DENIAL_FLOOD
        return SafetyState.OK, None

    def refresh(
        self,
        scope: str | None,
        code: str,
        trace_id: str,
        event: str = "status",
        actor: str | None = None,
        detail: str = "",
    ) -> None:
        """Recompute state for a scope (None = global, fans out to all devices)."""
        with self._lock:
            targets: list[str | None] = [scope]
            if scope is None:
                targets += sorted(self._devices)
            for target in targets:
                state, drop = self._state_for(target)
                self._emit(
                    self.tracker.update(target, state, drop, code, trace_id, event, actor, detail)
                )

    def known(self, device_id: str) -> None:
        with self._lock:
            self._devices.add(device_id)
        self.refresh(device_id, DecisionCode.OK, self.new_trace(), "registered")

    def check_estop(self, device_id: str, action: str) -> None:
        """Deny everything except reads while a latch covers the device."""
        rec = self.latch.latched_for(device_id)
        if rec and not action.startswith("read_"):
            raise SafetyError(
                DecisionCode.ESTOP_LATCHED,
                f"Emergency stop latched ({rec.scope}): {rec.reason}. Authorized reset required.",
            )

    def check_flood(self, device_id: str, action: str) -> None:
        if self.flood.blocked(device_id, action):
            raise SafetyError(
                DecisionCode.DENIAL_FLOOD,
                f"Too many denied '{action}' commands for '{device_id}'; throttled",
            )

    def trip(
        self,
        device_id: str | None,
        reason: str,
        source: str,
        code: str = DecisionCode.ESTOP_LATCHED,
        trace_id: str | None = None,
    ) -> LatchRecord | None:
        """Latch an e-stop for a device (None = global) and publish the transition."""
        trace = trace_id or self.new_trace()
        scope = GLOBAL_SCOPE if device_id is None else device_id
        with self._lock:
            rec = self.latch.trip(scope, reason, source, trace)
            if rec:
                self.refresh(device_id, code, trace, "estop_tripped", source, reason)
        return rec

    def reset(self, device_id: str | None, reason: str, who: str) -> LatchRecord | None:
        """Authorized reset; raises SafetyError when refused."""
        scope = GLOBAL_SCOPE if device_id is None else device_id
        trace = self.new_trace()
        with self._lock:
            rec = self.latch.reset(scope, who, reason)
            if rec:
                self.refresh(device_id, DecisionCode.OK, trace, "estop_reset", who, reason)
        return rec

    def observe(self, device_id: str, action: str, code: str, trace_id: str) -> None:
        """Count a denial, then publish any resulting status change."""
        with self._lock:
            self.flood.record(device_id, action, code)
            if device_id in self._devices:
                self.refresh(device_id, code, trace_id, "flood" if code else "status")

    def tick(self) -> None:
        """Re-evaluate every device (clears degraded once windows lapse)."""
        with self._lock:
            for device_id in sorted(self._devices):
                self.refresh(device_id, DecisionCode.OK, self.new_trace(), "tick")
