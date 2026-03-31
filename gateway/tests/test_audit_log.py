"""Tests for the audit log."""

import pytest

from gateway.audit_log import AuditLog


@pytest.fixture
async def audit_log() -> AuditLog:
    """Create an in-memory audit log for testing."""
    log = AuditLog(":memory:")
    await log.initialize()
    yield log  # type: ignore[misc]
    await log.close()


@pytest.mark.asyncio
async def test_record_and_retrieve(audit_log: AuditLog) -> None:
    """Record a command and retrieve it."""
    row_id = await audit_log.record(
        device_id="test-001",
        action="do_set_relay",
        params={"state": True},
        result={"status": "ok"},
        confirmed=True,
    )
    assert row_id > 0

    entries = await audit_log.get_recent(device_id="test-001")
    assert len(entries) == 1
    assert entries[0]["device_id"] == "test-001"
    assert entries[0]["action"] == "do_set_relay"
    assert entries[0]["params"] == {"state": True}
    assert entries[0]["confirmed"] is True


@pytest.mark.asyncio
async def test_multiple_entries(audit_log: AuditLog) -> None:
    """Multiple entries should be returned in reverse chronological order."""
    await audit_log.record("dev-1", "read_temp", {}, {"value": 23.5})
    await audit_log.record("dev-1", "do_set_relay", {"state": True}, {"status": "ok"})
    await audit_log.record("dev-2", "read_temp", {}, {"value": 20.0})

    # All entries
    all_entries = await audit_log.get_recent()
    assert len(all_entries) == 3

    # Filtered by device
    dev1 = await audit_log.get_recent(device_id="dev-1")
    assert len(dev1) == 2

    dev2 = await audit_log.get_recent(device_id="dev-2")
    assert len(dev2) == 1


@pytest.mark.asyncio
async def test_limit(audit_log: AuditLog) -> None:
    """Limit parameter should restrict results."""
    for i in range(10):
        await audit_log.record("dev-1", f"action_{i}", {}, {})

    entries = await audit_log.get_recent(limit=3)
    assert len(entries) == 3


@pytest.mark.asyncio
async def test_source_field(audit_log: AuditLog) -> None:
    """Source field should be stored correctly."""
    await audit_log.record(
        "dev-1", "read_temp", {}, {"value": 22},
        source="cli",
    )
    entries = await audit_log.get_recent()
    assert entries[0]["source"] == "cli"
