"""Sensor-data provenance: every sensor-derived payload carries origin and taint_id.

Pure logic, deterministic, injectable clock. The `taint_id` is stable: it is
derived from origin, source device and content hash (not the timestamp), so the
same data from the same source always yields the same id.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

Clock = Callable[[], float]


class Origin(StrEnum):
    """Who vouches for a piece of data."""

    TRUSTED_OPERATOR = "trusted_operator"
    DEVICE_TELEMETRY = "device_telemetry"
    UNTRUSTED_PERCEPTION = "untrusted_perception"


def parse_origin(value: object) -> Origin:
    """Parse an origin, failing closed: anything unknown is untrusted perception."""
    if isinstance(value, Origin):
        return value
    if isinstance(value, str):
        try:
            return Origin(value)
        except ValueError:
            pass
    return Origin.UNTRUSTED_PERCEPTION


def content_hash(payload: Any) -> str:
    """SHA-256 of a canonical JSON encoding (unserializable parts use repr)."""
    try:
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=repr)
    except (TypeError, ValueError):
        text = repr(payload)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Provenance:
    """Where a payload came from."""

    origin: Origin
    source_device: str
    timestamp: float
    content_hash: str

    @property
    def taint_id(self) -> str:
        """Stable id for this origin, source and content."""
        raw = f"{self.origin.value}|{self.source_device}|{self.content_hash}"
        return "t_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @property
    def untrusted(self) -> bool:
        return self.origin is Origin.UNTRUSTED_PERCEPTION

    def to_dict(self) -> dict[str, Any]:
        return {
            "origin": self.origin.value,
            "source_device": self.source_device,
            "timestamp": self.timestamp,
            "content_hash": self.content_hash,
            "taint_id": self.taint_id,
        }

    @classmethod
    def from_dict(cls, data: object) -> "Provenance | None":
        """Rebuild from a dict; None if malformed (callers must treat as tainted)."""
        if not isinstance(data, dict):
            return None
        ts = data.get("timestamp")
        dev = data.get("source_device")
        digest = data.get("content_hash")
        if isinstance(ts, bool) or not isinstance(ts, int | float):
            return None
        if not isinstance(dev, str) or not isinstance(digest, str):
            return None
        return cls(parse_origin(data.get("origin")), dev, float(ts), digest)


def make_provenance(
    payload: Any, origin: object, source_device: object, clock: Clock
) -> Provenance:
    """Build provenance for a payload; malformed inputs fail closed (untrusted)."""
    device = source_device if isinstance(source_device, str) and source_device else "unknown"
    return Provenance(parse_origin(origin), device, float(clock()), content_hash(payload))


def wrap(payload: Any, origin: object, source_device: object, clock: Clock) -> dict[str, Any]:
    """Wrap a payload as {origin, taint_id, provenance, data}."""
    prov = make_provenance(payload, origin, source_device, clock)
    return {
        "origin": prov.origin.value,
        "taint_id": prov.taint_id,
        "provenance": prov.to_dict(),
        "data": payload,
    }


def wrap_tool_result(result: Any, device: object, clock: Clock) -> dict[str, Any]:
    """Wrap a perception tool result (camera, OCR, STT, range scan): untrusted."""
    return wrap(result, Origin.UNTRUSTED_PERCEPTION, device, clock)


def wrap_telemetry(telemetry: Any, device: object, clock: Clock) -> dict[str, Any]:
    """Wrap device self-reported state (joint angles, battery)."""
    return wrap(telemetry, Origin.DEVICE_TELEMETRY, device, clock)


def wrap_scene_text(text: str, device: object, clock: Clock) -> dict[str, Any]:
    """Wrap scene text (signs, speech transcripts): always untrusted."""
    return wrap({"text": text}, Origin.UNTRUSTED_PERCEPTION, device, clock)


def wrap_operator_instruction(text: str, operator: object, clock: Clock) -> dict[str, Any]:
    """Wrap an operator instruction; this resets the taint window."""
    return wrap({"text": text}, Origin.TRUSTED_OPERATOR, operator, clock)


def taint_ids_since_operator(entries: list[dict[str, Any]]) -> set[str]:
    """Untrusted taint_ids seen since the last operator entry in a wrapped sequence.

    Malformed entries count as tainted (fail closed) under a derived id.
    """
    ids: set[str] = set()
    for entry in entries:
        prov = Provenance.from_dict(entry.get("provenance")) if isinstance(entry, dict) else None
        if prov is None:
            ids.add("t_malformed_" + content_hash(entry)[:12])
        elif prov.origin is Origin.TRUSTED_OPERATOR:
            ids.clear()
        elif prov.untrusted:
            ids.add(prov.taint_id)
    return ids
