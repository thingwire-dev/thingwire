"""Tests for device roster persistence."""

import json

from thingwire.device_roster import DeviceRoster


def test_save_and_load(tmp_path: object) -> None:
    """Save devices, load them back."""
    path = f"{tmp_path}/roster.json"  # type: ignore[operator]
    roster = DeviceRoster(path)

    devices = {"dev-001": {"title": "Test Device", "id": "urn:test"}}
    roster.save(devices)

    roster2 = DeviceRoster(path)
    loaded = roster2.load()
    assert loaded == devices


def test_load_missing_file(tmp_path: object) -> None:
    """Loading from non-existent file returns empty dict."""
    path = f"{tmp_path}/nope.json"  # type: ignore[operator]
    roster = DeviceRoster(path)
    assert roster.load() == {}


def test_add_device_persists(tmp_path: object) -> None:
    """add_device should persist immediately."""
    path = f"{tmp_path}/roster.json"  # type: ignore[operator]
    roster = DeviceRoster(path)
    roster.load()

    roster.add_device("dev-001", {"title": "A"})
    roster.add_device("dev-002", {"title": "B"})

    roster2 = DeviceRoster(path)
    loaded = roster2.load()
    assert "dev-001" in loaded
    assert "dev-002" in loaded


def test_remove_device(tmp_path: object) -> None:
    """remove_device should persist immediately."""
    path = f"{tmp_path}/roster.json"  # type: ignore[operator]
    roster = DeviceRoster(path)
    roster.load()

    roster.add_device("dev-001", {"title": "A"})
    roster.remove_device("dev-001")

    roster2 = DeviceRoster(path)
    loaded = roster2.load()
    assert "dev-001" not in loaded
