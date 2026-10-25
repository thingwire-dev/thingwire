"""Numeric plausibility checks for range scans (RIPA A2/A3 style falsified data).

Pure logic with an injectable clock. Malformed input fails closed: it produces
a finding and `ok` is False.
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

Clock = Callable[[], float]

MALFORMED = "SCAN_MALFORMED"
NON_FINITE = "SCAN_NON_FINITE"
OUT_OF_RANGE = "SCAN_OUT_OF_RANGE"
ALL_BLOCKED = "SCAN_ALL_BLOCKED"
ALL_IDENTICAL = "SCAN_ALL_IDENTICAL"
IMPLAUSIBLE_JUMP = "SCAN_IMPLAUSIBLE_JUMP"
BAD_TIMESTAMP = "SCAN_BAD_TIMESTAMP"
LENGTH_CHANGED = "SCAN_LENGTH_CHANGED"


@dataclass(frozen=True)
class ScanSpec:
    """Sensor limits; every threshold is a parameter, none is hardcoded in logic."""

    range_min: float
    range_max: float
    max_change_m_per_s: float
    sectors: int = 8
    blocked_below_m: float = 1.0
    min_samples: int = 2


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlausibilityResult:
    findings: tuple[Finding, ...]

    @property
    def ok(self) -> bool:
        return not self.findings

    @property
    def codes(self) -> list[str]:
        return [f.code for f in self.findings]


def _num(v: object) -> float | None:
    if isinstance(v, bool) or not isinstance(v, int | float):
        return None
    return float(v)


def _sector_mins(vals: list[float], sectors: int) -> list[float]:
    n = len(vals)
    return [min(vals[i * n // sectors : (i + 1) * n // sectors]) for i in range(sectors)]


class ScanChecker:
    """Checks a scan and, given the previous accepted scan, frame-to-frame jumps."""

    def __init__(self, spec: ScanSpec, clock: Clock) -> None:
        self._spec = spec
        self._clock = clock
        self._prev: tuple[list[float], float] | None = None

    def check(
        self, ranges: Sequence[object] | object, timestamp: float | None = None
    ) -> PlausibilityResult:
        spec = self._spec
        ts = float(self._clock()) if timestamp is None else timestamp
        if (
            not isinstance(ranges, Sequence)
            or isinstance(ranges, str | bytes)
            or len(ranges) < spec.min_samples
            or spec.sectors < 1
            or len(ranges) < spec.sectors
            or isinstance(ts, bool)
            or not isinstance(ts, int | float)
            or not math.isfinite(ts)
        ):
            return PlausibilityResult(
                (Finding(MALFORMED, "scan is not a usable numeric sequence"),)
            )
        nums = [_num(v) for v in ranges]
        bad = [i for i, v in enumerate(nums) if v is None]
        if bad:
            return PlausibilityResult(
                (Finding(MALFORMED, "non-numeric samples", {"indices": bad[:10]}),)
            )
        vals = [v for v in nums if v is not None]
        findings: list[Finding] = []
        nonfinite = [i for i, v in enumerate(vals) if not math.isfinite(v)]
        if nonfinite:
            findings.append(Finding(NON_FINITE, "NaN or inf samples", {"indices": nonfinite[:10]}))
        finite = [v for v in vals if math.isfinite(v)]
        outside = [
            i
            for i, v in enumerate(vals)
            if math.isfinite(v) and not spec.range_min <= v <= spec.range_max
        ]
        if outside:
            findings.append(
                Finding(
                    OUT_OF_RANGE,
                    "samples outside sensor min/max",
                    {"indices": outside[:10], "min": spec.range_min, "max": spec.range_max},
                )
            )
        if finite and not nonfinite:
            if len(set(vals)) == 1:
                findings.append(
                    Finding(ALL_IDENTICAL, "every sample is identical", {"value": vals[0]})
                )
            if all(m < spec.blocked_below_m for m in _sector_mins(vals, spec.sectors)):
                findings.append(
                    Finding(
                        ALL_BLOCKED, "all sectors blocked", {"threshold_m": spec.blocked_below_m}
                    )
                )
        findings.extend(self._jump_findings(vals, ts) if not nonfinite else [])
        if not findings:
            self._prev = (vals, ts)
        return PlausibilityResult(tuple(findings))

    def _jump_findings(self, vals: list[float], ts: float) -> list[Finding]:
        if self._prev is None:
            return []
        prev, prev_ts = self._prev
        dt = ts - prev_ts
        if dt <= 0:
            return [Finding(BAD_TIMESTAMP, "timestamp not after previous scan", {"dt": dt})]
        if len(prev) != len(vals):
            return [
                Finding(
                    LENGTH_CHANGED, "scan length changed", {"before": len(prev), "after": len(vals)}
                )
            ]
        worst = max(abs(a - b) for a, b in zip(vals, prev, strict=True)) / dt
        if worst > self._spec.max_change_m_per_s:
            return [
                Finding(
                    IMPLAUSIBLE_JUMP,
                    "frame-to-frame change exceeds max rate",
                    {"rate_m_per_s": worst, "max": self._spec.max_change_m_per_s},
                )
            ]
        return []
