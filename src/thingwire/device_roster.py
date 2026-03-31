"""Device roster — persist discovered device TDs to disk.

Saves Thing Descriptions to a JSON file so the gateway can restore
known devices on restart without waiting for re-discovery.
"""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class DeviceRoster:
    """Persists device Thing Descriptions to a JSON file."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._devices: dict[str, dict[str, Any]] = {}

    def load(self) -> dict[str, dict[str, Any]]:
        """Load saved devices from disk. Returns empty dict if file missing."""
        if not self._path.exists():
            logger.info("No saved roster at %s", self._path)
            return {}

        try:
            with open(self._path) as f:
                self._devices = json.load(f)
            logger.info("Loaded %d device(s) from roster", len(self._devices))
            return dict(self._devices)
        except (json.JSONDecodeError, OSError):
            logger.exception("Failed to load roster from %s", self._path)
            return {}

    def save(self, devices: dict[str, dict[str, Any]]) -> None:
        """Save current device map to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._path, "w") as f:
                json.dump(devices, f, indent=2)
            logger.debug("Saved %d device(s) to roster", len(devices))
        except OSError:
            logger.exception("Failed to save roster to %s", self._path)

    def add_device(self, device_id: str, td: dict[str, Any]) -> None:
        """Add or update a device and persist."""
        self._devices[device_id] = td
        self.save(self._devices)

    def remove_device(self, device_id: str) -> None:
        """Remove a device and persist."""
        self._devices.pop(device_id, None)
        self.save(self._devices)

    def get_devices(self) -> dict[str, dict[str, Any]]:
        """Return current in-memory device map."""
        return dict(self._devices)
