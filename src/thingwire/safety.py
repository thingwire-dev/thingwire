"""Safety layer — permission checks, rate limiting, confirmation, deadman switch.

Stateless checks with injectable rate limiter state. All actuator commands
MUST pass through this module before reaching the MQTT bridge.
"""

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class RateLimitWindow:
    """Sliding window rate limiter for a single action."""

    max_calls: int
    window_seconds: float
    timestamps: list[float] = field(default_factory=list)

    def check(self) -> bool:
        """Return True if action is within rate limit."""
        now = time.monotonic()
        # Prune old timestamps
        self.timestamps = [t for t in self.timestamps if now - t < self.window_seconds]
        return len(self.timestamps) < self.max_calls

    def record(self) -> None:
        """Record a call timestamp."""
        self.timestamps.append(time.monotonic())


class SafetyError(Exception):
    """Raised when a safety check fails."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class SafetyLayer:
    """Enforces safety policies for device commands."""

    def __init__(self, config_path: str | None = None) -> None:
        self._config: dict[str, Any] = {"devices": {}, "global": {}}
        self._rate_limiters: dict[str, dict[str, RateLimitWindow]] = {}
        self._last_heartbeat: dict[str, float] = {}

        if config_path and Path(config_path).exists():
            self._load_config(config_path)
        else:
            self._load_defaults()

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
            },
        }
        logger.info("Using default safety configuration")

    def register_device(self, device_id: str, td: dict[str, Any]) -> None:
        """Register a device and auto-generate safety rules from its TD."""
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
            if not action.get("safe", True):
                dangerous_actions.append(f"do_{name}")

        self._config.setdefault("devices", {})[device_id] = {
            "allowed_actions": allowed_actions,
            "dangerous_actions": dangerous_actions,
            "rate_limits": {},
        }
        logger.info(
            "Auto-registered safety rules for device %s: %d allowed, %d dangerous",
            device_id,
            len(allowed_actions),
            len(dangerous_actions),
        )

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

        # Get or create rate limiter
        if device_id not in self._rate_limiters:
            self._rate_limiters[device_id] = {}

        if action not in self._rate_limiters[device_id]:
            self._rate_limiters[device_id][action] = RateLimitWindow(
                max_calls=rate_config["max_calls"],
                window_seconds=rate_config["window_seconds"],
            )

        limiter = self._rate_limiters[device_id][action]
        if not limiter.check():
            raise SafetyError(
                "RATE_LIMIT_EXCEEDED",
                f"Rate limit exceeded for '{action}' on device '{device_id}'. "
                f"Max {limiter.max_calls} calls per {limiter.window_seconds}s",
            )

        limiter.record()

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
        self._last_heartbeat[device_id] = time.monotonic()

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

        elapsed = time.monotonic() - last
        if elapsed > timeout:
            raise SafetyError(
                "DEADMAN_SWITCH",
                f"Device '{device_id}' has not sent a heartbeat in {elapsed:.0f}s "
                f"(timeout: {timeout}s). Actuator commands are disabled for safety.",
            )

    def prune_rate_limiters(self) -> int:
        """Remove expired rate limit windows to prevent memory leak. Returns pruned count."""
        pruned = 0
        now = time.monotonic()
        for device_id in list(self._rate_limiters):
            for action in list(self._rate_limiters[device_id]):
                limiter = self._rate_limiters[device_id][action]
                limiter.timestamps = [
                    t for t in limiter.timestamps if now - t < limiter.window_seconds
                ]
                if not limiter.timestamps:
                    del self._rate_limiters[device_id][action]
                    pruned += 1
            if not self._rate_limiters[device_id]:
                del self._rate_limiters[device_id]
        return pruned
