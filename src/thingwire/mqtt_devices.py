"""Device state held by the MQTT bridge: TDs, latest telemetry, online status.

No MQTT I/O here. The bridge feeds raw payloads into the `_handle_*` methods and
the rest of the gateway reads state through the getters.
"""

import asyncio
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from thingwire.td_loader import ThingDescription, parse_thing_description_dict

logger = logging.getLogger(__name__)


class DeviceStore:
    """Discovered devices and their latest readings, keyed by MQTT device id."""

    def __init__(self) -> None:
        self._devices: dict[str, dict[str, Any]] = {}
        self._telemetry: dict[str, dict[str, Any]] = {}
        self._status: dict[str, str] = {}
        self._last_telemetry_time: dict[str, float] = {}  # device heartbeat from telemetry
        self.on_device_discovered: Callable[[str, dict[str, Any]], None] | None = None

    def _handle_td(self, device_id: str, payload: bytes) -> None:
        """Store raw Thing Description dict for a device."""
        try:
            td = json.loads(payload)
            is_new = device_id not in self._devices
            self._devices[device_id] = td
            logger.info("Discovered device: %s (%s)", device_id, td.get("title", "unknown"))
            if is_new and self.on_device_discovered:
                self.on_device_discovered(device_id, td)
        except json.JSONDecodeError:
            logger.error("Invalid TD JSON from device %s", device_id)

    def _handle_telemetry(self, device_id: str, payload: bytes) -> None:
        """Store latest telemetry readings and update heartbeat timestamp."""
        try:
            data = json.loads(payload)
            self._telemetry[device_id] = data
            self._last_telemetry_time[device_id] = time.monotonic()
            logger.debug("Telemetry from %s", device_id)
        except json.JSONDecodeError:
            logger.error("Invalid telemetry JSON from device %s", device_id)

    def _handle_status(self, device_id: str, payload: bytes) -> None:
        """Track device online/offline status."""
        status = payload.decode("utf-8", errors="replace").strip()
        if status in ("online", "offline"):
            self._status[device_id] = status
            logger.info("Device %s is now %s", device_id, status)
        else:
            logger.warning("Unknown status payload from %s: %r", device_id, status)

    async def wait_for_devices(self, timeout: float = 30.0) -> list[str]:
        """Wait up to timeout seconds for at least one device to be discovered."""
        deadline = asyncio.get_event_loop().time() + timeout
        while not self._devices:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.5, remaining))

        if self._devices:
            logger.info("Discovered %d device(s): %s", len(self._devices), list(self._devices))
        else:
            logger.warning("No devices discovered within %.0fs", timeout)

        return list(self._devices)

    def get_devices(self) -> list[str]:
        """Return list of discovered device IDs."""
        return list(self._devices)

    def get_td(self, device_id: str) -> ThingDescription | None:
        """Return parsed ThingDescription for a device, or None if unknown."""
        raw = self._devices.get(device_id)
        if raw is None:
            return None
        try:
            return parse_thing_description_dict(raw)
        except ValueError:
            logger.error("Could not parse stored TD for device %s", device_id)
            return None

    def get_latest_reading(self, device_id: str, property_name: str) -> dict[str, Any]:
        """Return latest reading dict for a specific property."""
        telemetry = self._telemetry.get(device_id)
        if not telemetry:
            return {
                "error": f"No telemetry available for device {device_id}",
                "device_id": device_id,
                "property": property_name,
            }

        readings = telemetry.get("readings", {})
        if property_name not in readings:
            return {
                "error": f"Property '{property_name}' not found in telemetry",
                "device_id": device_id,
                "available": list(readings),
            }

        reading = readings[property_name]
        return {
            "device_id": device_id,
            "property": property_name,
            "value": reading["value"],
            "unit": reading.get("unit"),
            "timestamp": telemetry.get("timestamp"),
        }

    def get_last_telemetry_time(self, device_id: str) -> float | None:
        """Return monotonic timestamp of last telemetry, or None if never received."""
        return self._last_telemetry_time.get(device_id)

    def is_device_online(self, device_id: str) -> bool:
        """Return True if the device last reported an online status."""
        return self._status.get(device_id) == "online"

    def get_device_status(self, device_id: str) -> str:
        """Return device status string: 'online', 'offline', or 'unknown'."""
        return self._status.get(device_id, "unknown")
