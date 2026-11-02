"""Command freshness: an issued_at window plus a bounded nonce replay cache (contracts 5.3).

Pure logic. The epoch clock is injected, so a replayed run decides identically. Every
failure path denies; the cache never evicts a live nonce, because that would let a replay in.
"""

import logging
import math
import re
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from thingwire.safety_codes import DecisionCode, SafetyError
from thingwire.safety_motion import Decision

logger = logging.getLogger(__name__)

EpochClock = Callable[[], float]
NONCE_RE = re.compile(r"[A-Za-z0-9_-]{8,128}")


def _flag(section: Mapping[str, Any], key: str, default: bool) -> bool:
    value = section.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"command_freshness.{key} must be a boolean")
    return value


def _seconds(section: Mapping[str, Any], key: str, default: float) -> float:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"command_freshness.{key} must be a number")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"command_freshness.{key} must be finite and not negative")
    return float(value)


@dataclass(frozen=True)
class FreshnessConfig:
    """`global.command_freshness`; the defaults here are the documented config defaults."""

    enabled: bool = True
    required: bool = False
    max_age_seconds: float = 30.0
    max_future_skew_seconds: float = 5.0
    nonce_window_seconds: float = 120.0
    max_nonces: int = 4096

    @classmethod
    def from_config(cls, global_cfg: Mapping[str, Any]) -> "FreshnessConfig":
        """Build from the `global` section. Raises ValueError on a malformed value."""
        section = global_cfg.get("command_freshness") or {}
        if not isinstance(section, Mapping):
            raise ValueError("command_freshness must be a mapping")
        base = cls()
        age = _seconds(section, "max_age_seconds", base.max_age_seconds)
        skew = _seconds(section, "max_future_skew_seconds", base.max_future_skew_seconds)
        window = _seconds(section, "nonce_window_seconds", base.nonce_window_seconds)
        cap = section.get("max_nonces", base.max_nonces)
        if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
            raise ValueError("command_freshness.max_nonces must be a positive integer")
        return cls(
            _flag(section, "enabled", base.enabled),
            _flag(section, "required", base.required),
            age,
            skew,
            max(window, age + skew),  # a nonce must outlive the stamp it belongs to
            cap,
        )

    def guard(self, epoch_clock: EpochClock = time.time) -> "FreshnessGuard":
        return FreshnessGuard(self, epoch_clock)


def _stale(reason: str, message: str, **details: Any) -> SafetyError:
    return SafetyError(DecisionCode.STALE_COMMAND, message, {"reason": reason, **details})


class FreshnessGuard:
    """Judges one command stamp at a time against the window and the nonce cache."""

    def __init__(self, cfg: FreshnessConfig, epoch_clock: EpochClock = time.time) -> None:
        self._cfg = cfg
        self._clock = epoch_clock
        self._seen: OrderedDict[tuple[str, str], float] = OrderedDict()

    def check(self, device_id: str, issued_at: object, nonce: object) -> None:
        """Raise SafetyError unless the stamp is fresh and its nonce unused."""
        cfg = self._cfg
        if not cfg.enabled:
            return
        if issued_at is None and nonce is None:
            if cfg.required:
                raise _stale("missing", "command carries no issued_at and nonce")
            return
        if (
            isinstance(issued_at, bool)
            or not isinstance(issued_at, int | float)
            or not math.isfinite(issued_at)
            or not isinstance(nonce, str)
            or NONCE_RE.fullmatch(nonce) is None
        ):
            raise _stale(
                "malformed", "issued_at must be a finite number and nonce 8-128 safe chars"
            )
        now = self._clock()
        age = now - float(issued_at)
        if age > cfg.max_age_seconds:
            raise _stale("too_old", f"command is {age:.1f}s old", age_seconds=age)
        if -age > cfg.max_future_skew_seconds:
            raise _stale("from_future", "command is dated in the future", age_seconds=age)
        self._prune(now)
        key = (device_id, nonce)
        if key in self._seen:
            raise SafetyError(
                DecisionCode.REPLAYED_COMMAND,
                "nonce was already used",
                {"reason": "nonce_reused"},
            )
        if len(self._seen) >= cfg.max_nonces:
            raise SafetyError(
                DecisionCode.SAFETY_CHECK_ERROR,
                "nonce cache is full; command denied (fail closed)",
                {"reason": "nonce_cache_full"},
            )
        self._seen[key] = now

    def judge(self, device_id: str, issued_at: object, nonce: object) -> Decision | None:
        """`check` as a Decision: None when fresh, a denial otherwise (never raises)."""
        try:
            self.check(device_id, issued_at, nonce)
            return None
        except SafetyError as exc:
            return Decision(False, exc.code, exc.message, dict(exc.details))
        except Exception as exc:
            logger.exception("Freshness check failed for %s, denying", device_id)
            msg = "Freshness check raised an error; command denied (fail closed)"
            return Decision(False, DecisionCode.SAFETY_CHECK_ERROR, msg, {"error": str(exc)})

    def _prune(self, now: float) -> None:
        window = self._cfg.nonce_window_seconds
        while self._seen:
            oldest = next(iter(self._seen.values()))
            if now - oldest <= window:
                break
            self._seen.popitem(last=False)
