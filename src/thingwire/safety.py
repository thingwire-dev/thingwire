"""Safety layer — permission checks, rate limiting, confirmation, deadman switch.

Stateless checks with injectable rate limiter state. All actuator commands
MUST pass through this module before reaching the MQTT bridge.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from thingwire.safety_codes import DecisionCode, SafetyError
from thingwire.safety_guards import SafetyGuards
from thingwire.safety_motion import AxisLimit, Decision, MotionGuard, extract_limits
from thingwire.safety_ratelimit import RateLimiterBook, RateLimitWindow
from thingwire.safety_status import SafetyTransition

__all__ = ["RateLimitWindow", "SafetyError", "SafetyLayer"]

logger = logging.getLogger(__name__)


class SafetyLayer:
    """Enforces safety policies for device commands."""

    def __init__(
        self,
        config_path: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        *,
        state_db_path: str | None = None,
        wall_clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._clock = clock
        self._limits: dict[tuple[str, str], dict[str, AxisLimit]] = {}
        self._config: dict[str, Any] = {"devices": {}, "global": {}}
        self._rates = RateLimiterBook(clock)
        self._rate_limiters = self._rates.limiters  # shared view
        self._last_heartbeat: dict[str, float] = {}

        if config_path and Path(config_path).exists():
            self._load_config(config_path)
        else:
            self._load_defaults()
        mode = str(self._config.get("global", {}).get("out_of_range", "reject")).lower()
        self._motion = MotionGuard(clock, "clamp" if mode == "clamp" else "reject")
        extra: dict[str, Any] = {"id_factory": id_factory} if id_factory else {}
        self.guards = SafetyGuards(
            self._config.get("global", {}), clock, state_db_path, wall_clock, **extra
        )

    def _load_config(self, config_path: str) -> None:
        """Load safety configuration from YAML."""
        try:
            with open(config_path) as f:
                self._config = yaml.safe_load(f) or {}
            logger.info("Loaded safety config from %s", config_path)
        except Exception:
            logger.exception("Failed to load safety config from %s, using defaults", config_path)
            self._load_defaults()

    def _load_defaults(self) -> None:
        """Load sensible default safety configuration."""
        self._config = {
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
        logger.info("Using default safety configuration")

    def register_device(self, device_id: str, td: dict[str, Any]) -> None:
        """Register a device and auto-generate safety rules from its TD."""
        self.guards.known(device_id)
        if device_id in self._config.get("devices", {}):
            return  # Already configured explicitly

        actions = td.get("actions", {})
        allowed_actions: list[str] = []
        dangerous_actions: list[str] = []

        # Auto-detect from TD
        for name in td.get("properties", {}):
            allowed_actions.append(f"read_{name}")
        for name, action in actions.items():
            allowed_actions.append(f"do_{name}")
            limits = extract_limits(action)
            if limits:
                self._limits[(device_id, f"do_{name}")] = limits
            if not action.get("safe", True):
                dangerous_actions.append(f"do_{name}")

        self._config.setdefault("devices", {})[device_id] = {
            "allowed_actions": allowed_actions,
            "dangerous_actions": dangerous_actions,
            "rate_limits": {},
        }
        logger.info("Auto-registered safety rules for device %s", device_id)

    def check_permission(self, device_id: str, action: str) -> None:
        """Check if action is allowed for device. Raises SafetyError if not."""
        device_config = self._config.get("devices", {}).get(device_id)

        if not device_config:
            raise SafetyError(
                "DEVICE_NOT_REGISTERED",
                f"Device '{device_id}' is not registered in safety config",
            )

        allowed = device_config.get("allowed_actions", [])
        if allowed and action not in allowed:
            raise SafetyError(
                "ACTION_NOT_ALLOWED",
                f"Action '{action}' is not in the allowlist for device '{device_id}'. "
                f"Allowed: {allowed}",
            )

    def check_rate_limit(self, device_id: str, action: str) -> None:
        """Check rate limit for action. Raises SafetyError if exceeded."""
        device_config = self._config.get("devices", {}).get(device_id, {})
        rate_config = device_config.get("rate_limits", {}).get(action)

        if not rate_config:
            # Use global default
            rate_config = self._config.get("global", {}).get("default_rate_limit")

        if not rate_config:
            return  # No rate limit configured

        limiter = self._rates.check_and_record(device_id, action, rate_config)
        if limiter:
            raise SafetyError(
                DecisionCode.RATE_LIMIT_EXCEEDED,
                f"Rate limit exceeded for '{action}' on device '{device_id}'. "
                f"Max {limiter.max_calls} calls per {limiter.window_seconds}s",
            )

    def is_dangerous(self, device_id: str, action: str) -> bool:
        """Check if an action is tagged as dangerous."""
        device_config = self._config.get("devices", {}).get(device_id, {})
        return action in device_config.get("dangerous_actions", [])

    def require_confirmation(self, action: str, params: dict[str, Any]) -> str:
        """Return a confirmation message for dangerous actions.

        In v1, this logs a warning. In v2, this will integrate with
        MCP's user confirmation flow.
        """
        msg = (
            f"CONFIRMATION REQUIRED: Action '{action}' with params {params} "
            f"is marked as dangerous and affects physical hardware."
        )
        logger.warning(msg)
        return msg

    def record_heartbeat(self, device_id: str) -> None:
        """Record a heartbeat from a device (manual call)."""
        self._last_heartbeat[device_id] = self._clock()

    def update_heartbeat_from_telemetry(self, device_id: str, telemetry_time: float | None) -> None:
        """Update heartbeat using the bridge's telemetry timestamp.

        Called by the MCP server before safety checks to keep the deadman
        switch in sync with actual device telemetry, not just explicit
        heartbeat calls.
        """
        if telemetry_time is not None:
            self._last_heartbeat[device_id] = telemetry_time

    def check_deadman_switch(self, device_id: str) -> None:
        """Check if device is still alive. Raises SafetyError if heartbeat lost."""
        timeout = self._config.get("global", {}).get("deadman_switch_timeout_seconds", 300)
        last = self._last_heartbeat.get(device_id)

        if last is None:
            return  # No heartbeat tracking yet — allow first command

        elapsed = self._clock() - last
        if elapsed > timeout:
            raise SafetyError(
                "DEADMAN_SWITCH",
                f"Device '{device_id}' has not sent a heartbeat in {elapsed:.0f}s "
                f"(timeout: {timeout}s). Actuator commands are disabled for safety.",
            )

    def prune_rate_limiters(self) -> int:
        """Remove expired rate limit windows to prevent memory leak. Returns pruned count."""
        return self._rates.prune()

    def add_transition_listener(self, listener: Callable[[SafetyTransition], None]) -> None:
        """Subscribe to safety state transitions (must not block)."""
        self.guards.add_listener(listener)

    def trip_estop(self, device_id: str | None, reason: str, source: str = "api") -> bool:
        """Latch an e-stop for a device (None = global). True if newly latched."""
        return self.guards.trip(device_id, reason, source) is not None

    def reset_estop(self, device_id: str | None, reason: str, who: str) -> bool:
        """Authorized reset. Raises SafetyError if refused. True if a latch was cleared."""
        return self.guards.reset(device_id, reason, who) is not None

    def estop_latched(self, device_id: str) -> bool:
        """True if the device (or global scope) is latched."""
        return self.guards.latch.latched_for(device_id) is not None

    def sweep_deadman(self) -> None:
        """Trip e-stop for every device whose heartbeat expired, then refresh statuses."""
        for device_id in list(self._last_heartbeat):
            try:
                self.check_deadman_switch(device_id)
            except SafetyError as exc:
                self.guards.trip(device_id, exc.message, "deadman", DecisionCode.DEADMAN_SWITCH)
        self.guards.tick()

    def evaluate(
        self,
        device_id: str,
        action: str,
        params: dict[str, Any],
        *,
        approved: bool = False,
        agent_id: str | None = None,
        delegation_chain: list[str] | None = None,
        scopes: list[str] | None = None,
    ) -> Decision:
        """Run every check and return a structured Decision. Fails closed on any error.

        Identity arguments are accepted for the audit trail; they do not alter the verdict.
        Every decision carries a trace_id in its details.
        """
        trace = self.guards.new_trace()
        try:
            verdict = self._evaluate(device_id, action, params, approved)
        except SafetyError as exc:
            verdict = Decision(False, exc.code, exc.message, {"agent_id": agent_id})
        except Exception as exc:
            logger.exception("Safety check failed for %s/%s, denying", device_id, action)
            verdict = Decision(
                False,
                DecisionCode.SAFETY_CHECK_ERROR,
                "Safety check raised an error; command denied (fail closed)",
                {"error": f"{type(exc).__name__}: {exc}"},
            )
        try:
            if not verdict.allowed:
                self.guards.observe(device_id, action, verdict.code, trace)
        except Exception:
            logger.exception("Safety status update failed for %s", device_id)
        return replace(verdict, details={**verdict.details, "trace_id": trace})

    def _evaluate(
        self, device_id: str, action: str, params: dict[str, Any], approved: bool
    ) -> Decision:
        self.guards.check_estop(device_id, action)
        self.guards.check_flood(device_id, action)
        self.check_permission(device_id, action)
        try:
            self.check_deadman_switch(device_id)
        except SafetyError as exc:
            self.guards.trip(device_id, exc.message, "deadman", DecisionCode.DEADMAN_SWITCH)
            raise
        required = self._config.get("global", {}).get("require_confirmation_for_dangerous", True)
        if required and self.is_dangerous(device_id, action) and not approved:
            return Decision(
                False,
                DecisionCode.APPROVAL_REQUIRED,
                self.require_confirmation(action, params),
                {"action": action},
            )
        limits = self._limits.get((device_id, action), {})
        verdict = self._motion.check(device_id, action, params, limits)
        if not verdict.allowed:
            return verdict
        self.check_rate_limit(device_id, action)
        self._motion.commit(device_id, action, verdict.params)
        return verdict
