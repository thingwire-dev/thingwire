"""Denial flood guard: throttles repeated denials per (device, action)."""

from collections.abc import Callable, Iterable


class DenialFloodGuard:
    """Counts counted-code denials in a sliding window. Disabled when threshold <= 0."""

    def __init__(
        self,
        threshold: int,
        window_seconds: float,
        counted_codes: Iterable[str],
        clock: Callable[[], float],
    ) -> None:
        self._threshold = threshold
        self._window = window_seconds
        self._codes = frozenset(counted_codes)
        self._clock = clock
        self._hits: dict[tuple[str, str], list[float]] = {}

    def _live(self, key: tuple[str, str]) -> list[float]:
        now = self._clock()
        live = [t for t in self._hits.get(key, []) if now - t < self._window]
        if live:
            self._hits[key] = live
        else:
            self._hits.pop(key, None)
        return live

    def blocked(self, device_id: str, action: str) -> bool:
        """True if this (device, action) already hit the threshold in the window."""
        return self._threshold > 0 and len(self._live((device_id, action))) >= self._threshold

    def record(self, device_id: str, action: str, code: str) -> None:
        """Count a denial if its code is configured as counted."""
        if self._threshold > 0 and code in self._codes:
            key = (device_id, action)
            self._live(key)
            self._hits.setdefault(key, []).append(self._clock())

    def active(self, device_id: str) -> bool:
        """True if any action on the device is currently flood-blocked."""
        return any(d == device_id and self.blocked(d, a) for d, a in list(self._hits))
