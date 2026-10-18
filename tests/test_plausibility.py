"""Plausibility tests for RIPA channel 3 style falsified scans (data only)."""

import math
from typing import Any

import pytest

from thingwire import plausibility as pl
from thingwire.plausibility import ScanChecker, ScanSpec

SPEC = ScanSpec(
    range_min=0.12, range_max=3.5, max_change_m_per_s=2.0, sectors=8, blocked_below_m=1.0
)
N = 16


def checker() -> ScanChecker:
    return ScanChecker(SPEC, lambda: 0.0)


def varied(base: float = 2.0) -> list[float]:
    return [base + 0.01 * i for i in range(N)]


def sectors_with(blocked: list[int], near: float = 0.5, far: float = 3.0) -> list[float]:
    per = N // 8
    return [near + 0.01 * i if i // per in blocked else far + 0.01 * i for i in range(N)]


@pytest.mark.parametrize(
    ("name", "scan", "expected"),
    [
        ("clean", varied(), []),
        ("A1 front blocked only, plausible", sectors_with([0]), []),
        ("A2 all sectors blocked", sectors_with(list(range(8))), [pl.ALL_BLOCKED]),
        ("all identical in range", [2.0] * N, [pl.ALL_IDENTICAL]),
        ("A3 ghosts on all but front, plausible", sectors_with(list(range(1, 8))), []),
        ("all identical and blocked", [0.5] * N, [pl.ALL_IDENTICAL, pl.ALL_BLOCKED]),
        ("below min", [*varied()[:-1], 0.01], [pl.OUT_OF_RANGE]),
        ("above max", [*varied()[:-1], 99.0], [pl.OUT_OF_RANGE]),
        ("nan", [*varied()[:-1], math.nan], [pl.NON_FINITE]),
        ("inf", [*varied()[:-1], math.inf], [pl.NON_FINITE]),
        ("empty", [], [pl.MALFORMED]),
        ("too short", [1.0], [pl.MALFORMED]),
        ("strings", ["a"] * N, [pl.MALFORMED]),
        ("bool", [True] * N, [pl.MALFORMED]),
        ("not a sequence", 5, [pl.MALFORMED]),
        ("str", "abcdefghijklmnop", [pl.MALFORMED]),
    ],
)
def test_single_scan(name: str, scan: Any, expected: list[str]) -> None:
    assert checker().check(scan).codes == expected, name


def test_jump_flagged_after_accepted_frame() -> None:
    c = checker()
    assert c.check(varied(1.0), 10.0).ok
    r = c.check(varied(3.0), 10.5)  # 2 m in 0.5 s = 4 m/s
    assert r.codes == [pl.IMPLAUSIBLE_JUMP]


def test_slow_change_ok() -> None:
    c = checker()
    assert c.check(varied(1.0), 10.0).ok
    assert c.check(varied(1.5), 11.0).ok


def test_rejected_frame_not_used_as_baseline() -> None:
    c = checker()
    c.check(varied(1.0), 10.0)
    assert not c.check(varied(3.0), 10.5).ok
    assert not c.check(varied(3.0), 10.6).ok


@pytest.mark.parametrize(("ts", "code"), [(10.0, pl.BAD_TIMESTAMP), (9.0, pl.BAD_TIMESTAMP)])
def test_non_monotonic_time(ts: float, code: str) -> None:
    c = checker()
    c.check(varied(), 10.0)
    assert c.check(varied(), ts).codes == [code]


def test_length_change() -> None:
    c = checker()
    c.check(varied(), 1.0)
    assert c.check(varied()[:8], 2.0).codes == [pl.LENGTH_CHANGED]


def test_injectable_clock_used() -> None:
    t = iter([1.0, 2.0])
    c = ScanChecker(SPEC, lambda: next(t))
    assert c.check(varied()).ok
    assert c.check(varied()).ok


@pytest.mark.parametrize("ts", [math.nan, True, "x"])
def test_bad_timestamp_fails_closed(ts: Any) -> None:
    assert checker().check(varied(), ts).codes == [pl.MALFORMED]
