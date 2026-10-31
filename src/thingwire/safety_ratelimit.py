"""Sliding window rate limiter."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class RateLimitWindow:
    """Sliding window rate limiter for a single action."""

    max_calls: int
    window_seconds: float
    timestamps: list[float] = field(default_factory=list)
    clock: Callable[[], float] = time.monotonic

    def check(self) -> bool:
        """Return True if action is within rate limit."""
        now = self.clock()
        self.timestamps = [t for t in self.timestamps if now - t < self.window_seconds]
        return len(self.timestamps) < self.max_calls

    def record(self) -> None:
        """Record a call timestamp."""
        self.timestamps.append(self.clock())


class RateLimiterBook:
    """Per-action windows plus one per-device window across every action (contracts 5.2)."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self.limiters: dict[str, dict[str, RateLimitWindow]] = {}
        self.device_limiters: dict[str, RateLimitWindow] = {}

    def _window(self, cfg: dict[str, float]) -> RateLimitWindow:
        return RateLimitWindow(
            max_calls=int(cfg["max_calls"]),
            window_seconds=float(cfg["window_seconds"]),
            clock=self._clock,
        )

    def admit(
        self,
        device_id: str,
        action: str,
        action_cfg: dict[str, float] | None,
        device_cfg: dict[str, float] | None,
    ) -> tuple[str, RateLimitWindow] | None:
        """Record a call in every applicable window, or in none.

        Returns (scope, limiter) for the first window that is full, else None.
        """
        windows: list[tuple[str, RateLimitWindow]] = []
        if device_cfg:
            if device_id not in self.device_limiters:
                self.device_limiters[device_id] = self._window(device_cfg)
            windows.append(("device", self.device_limiters[device_id]))
        if action_cfg:
            per_device = self.limiters.setdefault(device_id, {})
            if action not in per_device:
                per_device[action] = self._window(action_cfg)
            windows.append(("action", per_device[action]))
        for scope, limiter in windows:
            if not limiter.check():
                return scope, limiter
        for _, limiter in windows:
            limiter.record()
        return None

    def prune(self) -> int:
        """Remove expired windows. Returns pruned count."""
        pruned = 0
        now = self._clock()
        for device_id in list(self.limiters):
            for action in list(self.limiters[device_id]):
                limiter = self.limiters[device_id][action]
                limiter.timestamps = [
                    t for t in limiter.timestamps if now - t < limiter.window_seconds
                ]
                if not limiter.timestamps:
                    del self.limiters[device_id][action]
                    pruned += 1
            if not self.limiters[device_id]:
                del self.limiters[device_id]
        for device_id in list(self.device_limiters):
            window = self.device_limiters[device_id]
            window.timestamps = [t for t in window.timestamps if now - t < window.window_seconds]
            if not window.timestamps:
                del self.device_limiters[device_id]
                pruned += 1
        return pruned
