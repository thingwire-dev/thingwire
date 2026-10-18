"""Tests for the audit log."""

from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest

from thingwire.audit_log import AuditLog


@pytest.fixture
async def audit_log() -> AsyncIterator[AuditLog]:
    """Create an in-memory audit log for testing."""
    log = AuditLog(":memory:")
    await log.initialize()
    yield log
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
        "dev-1",
        "read_temp",
        {},
        {"value": 22},
        source="cli",
    )
    entries = await audit_log.get_recent()
    assert entries[0]["source"] == "cli"


NEW_COLUMNS = {"agent_id", "delegation_chain", "scopes", "decision", "reason"}

OLD_SCHEMA = """
CREATE TABLE commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    device_id TEXT NOT NULL,
    action TEXT NOT NULL,
    params_json TEXT,
    result_json TEXT,
    confirmed INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'mcp'
)
"""


@pytest.mark.asyncio
async def test_identity_and_decision_fields(audit_log: AuditLog) -> None:
    from thingwire.safety_motion import Decision

    verdict = Decision(False, "OUT_OF_RANGE", "too far", {"axis": "angle"})
    await audit_log.record_decision(
        "servo",
        "do_setAngle",
        {"angle": 999},
        verdict,
        agent_id="agent-1",
        delegation_chain=["user", "agent-1"],
        scopes=["act:servo"],
    )
    row = (await audit_log.get_recent())[0]
    assert row["agent_id"] == "agent-1"
    assert row["delegation_chain"] == ["user", "agent-1"]
    assert row["scopes"] == ["act:servo"]
    assert (row["decision"], row["reason"]) == ("deny", "OUT_OF_RANGE")
    assert row["result"]["details"] == {"axis": "angle"}


@pytest.mark.asyncio
async def test_old_database_migrates_idempotently(tmp_path: Path) -> None:
    db_path = str(tmp_path / "old.db")
    async with aiosqlite.connect(db_path) as db:
        await db.execute(OLD_SCHEMA)
        await db.execute(
            "INSERT INTO commands (timestamp, device_id, action, params_json) "
            "VALUES ('2999-01-01T00:00:00Z', 'd', 'a', '{}')"
        )
        await db.commit()

    for _ in range(2):
        log = AuditLog(db_path)
        await log.initialize()
        await log.record("d", "b", {}, {}, decision="allow", reason="OK")
        await log.close()

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("PRAGMA table_info(commands)")
        cols = {row[1] for row in await cursor.fetchall()}
    assert cols >= NEW_COLUMNS

    log = AuditLog(db_path)
    await log.initialize()
    rows = await log.get_recent()
    await log.close()
    assert len(rows) == 3
    assert {r["action"] for r in rows} == {"a", "b"}
    old = next(r for r in rows if r["action"] == "a")
    assert old["agent_id"] is None
    assert old["decision"] is None
