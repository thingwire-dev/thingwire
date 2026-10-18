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
    """Per-device, per-action rate limiters."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self.limiters: dict[str, dict[str, RateLimitWindow]] = {}

    def check_and_record(
        self, device_id: str, action: str, cfg: dict[str, float]
    ) -> RateLimitWindow | None:
        """Record a call. Returns the limiter if the limit was exceeded, else None."""
        per_device = self.limiters.setdefault(device_id, {})
        if action not in per_device:
            per_device[action] = RateLimitWindow(
                max_calls=int(cfg["max_calls"]),
                window_seconds=cfg["window_seconds"],
                clock=self._clock,
            )
        limiter = per_device[action]
        if not limiter.check():
            return limiter
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
        return pruned
