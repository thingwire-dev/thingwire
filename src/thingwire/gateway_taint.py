"""Taint tracking for the gateway: which numbers did an agent read from untrusted sensors?

Read tools hand values to an agent. Each read is wrapped with provenance (origin,
taint_id) and its numeric values are kept in a per-session ledger for a bounded
window. When the same agent later calls an action whose numeric arguments match a
ledger value, that provenance is the action's taint context and `TaintPolicy`
decides. Range scans configured under `taint.scans` also go through the
plausibility checker; an implausible scan is always treated as untrusted.

Config (all optional, under the `taint:` section of safety_config.yaml):
    perception_properties: [person_detected]   # TD properties that are perception
    context_window_seconds: 300                 # how long a read value stays in context
    max_context_entries: 256                    # ledger bound per session
    scans: {ranges: {range_min: 0.1, range_max: 12, max_change_m_per_s: 5}}
"""

import logging
import math
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from thingwire.plausibility import ScanChecker, ScanSpec
from thingwire.provenance import Origin, Provenance, make_provenance
from thingwire.safety_motion import Decision
from thingwire.taint_policy import TaintPolicy

logger = logging.getLogger(__name__)

Clock = Callable[[], float]
LOCAL_SESSION = "local"


@dataclass(frozen=True)
class _Seen:
    values: frozenset[float]
    prov: Provenance
    at: float


def _numbers(value: Any) -> set[float]:
    """Finite numbers in a reading (a scalar or a flat list such as a range scan)."""
    items = value if isinstance(value, list | tuple) else [value]
    out: set[float] = set()
    for item in items:
        if isinstance(item, bool) or not isinstance(item, int | float):
            continue
        if math.isfinite(float(item)):
            out.add(float(item))
    return out


def _scan_specs(section: Mapping[str, Any]) -> dict[str, ScanSpec]:
    scans = section.get("scans") or {}
    if not isinstance(scans, Mapping):
        raise ValueError("taint.scans must be a mapping")
    return {str(name): ScanSpec(**dict(spec)) for name, spec in scans.items()}


class TaintTracker:
    """Per-session ledger of sensor-derived values plus the configured TaintPolicy."""

    def __init__(
        self,
        policy: TaintPolicy,
        *,
        perception_properties: frozenset[str] = frozenset(),
        scans: Mapping[str, ScanSpec] | None = None,
        window_seconds: float = 300.0,
        max_entries: int = 256,
        clock: Clock = time.monotonic,
    ) -> None:
        self.policy = policy
        self._perception = perception_properties
        self._scan_specs = dict(scans or {})
        self._checkers: dict[tuple[str, str], ScanChecker] = {}
        self._window = window_seconds
        self._max = max_entries
        self._clock = clock
        self._ledger: dict[str, deque[_Seen]] = {}

    @classmethod
    def from_config(
        cls, config: Mapping[str, Any] | None, clock: Clock = time.monotonic
    ) -> "TaintTracker":
        """Build from the full safety config. Raises ValueError on a malformed section."""
        section = (config or {}).get("taint") or {}
        if not isinstance(section, Mapping):
            raise ValueError("taint config must be a mapping")
        perception = section.get("perception_properties") or []
        if not isinstance(perception, list):
            raise ValueError("taint.perception_properties must be a list")
        return cls(
            TaintPolicy.from_config(config),
            perception_properties=frozenset(str(p) for p in perception),
            scans=_scan_specs(section),
            window_seconds=float(section.get("context_window_seconds", 300.0)),
            max_entries=int(section.get("max_context_entries", 256)),
            clock=clock,
        )

    def _prune(self, entries: deque[_Seen], now: float) -> None:
        while entries and (now - entries[0].at > self._window or len(entries) > self._max):
            entries.popleft()

    def observe_read(
        self, session: str, device_id: str, prop: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        """Wrap a read result with provenance and remember its numbers. Returns a new dict."""
        if "value" not in result:
            return result
        value = result["value"]
        origin = (
            Origin.UNTRUSTED_PERCEPTION if prop in self._perception else (Origin.DEVICE_TELEMETRY)
        )
        out = dict(result)
        spec = self._scan_specs.get(prop)
        if spec is not None:
            checker = self._checkers.setdefault((device_id, prop), ScanChecker(spec, self._clock))
            verdict = checker.check(value)
            out["plausible"] = verdict.ok
            if not verdict.ok:
                out["plausibility_findings"] = verdict.codes
                origin = Origin.UNTRUSTED_PERCEPTION
        now = self._clock()
        prov = make_provenance(value, origin, device_id, lambda: now)
        out["origin"] = prov.origin.value
        out["taint_id"] = prov.taint_id
        numbers = _numbers(value)
        if numbers:
            entries = self._ledger.setdefault(session, deque())
            entries.append(_Seen(frozenset(numbers), prov, now))
            self._prune(entries, now)
        return out

    def context_for(self, session: str, params: Mapping[str, Any]) -> list[Provenance]:
        """Provenance of ledger values that reappear in an action's numeric arguments."""
        wanted = set()
        for value in params.values():
            wanted |= _numbers(value)
        entries = self._ledger.get(session)
        if not wanted or not entries:
            return []
        self._prune(entries, self._clock())
        return [seen.prov for seen in entries if seen.values & wanted]

    def evaluate(
        self,
        session: str,
        action: str,
        params: Mapping[str, Any],
        *,
        granted_scopes: frozenset[str] = frozenset(),
        approved: bool = False,
    ) -> Decision:
        """TaintPolicy verdict for an action call. `approved` must come from theAuth only."""
        try:
            context = self.context_for(session, params)
            return self.policy.evaluate(
                action, context, granted_scopes=granted_scopes, approved=approved
            )
        except Exception:
            logger.exception("Taint evaluation failed for %s, denying", action)
            return Decision(False, "TAINT_MALFORMED_INPUT", "taint evaluation failed (fail closed)")
