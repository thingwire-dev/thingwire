"""Audit log hash chain: edits, deletions and reordering are detected (contracts 4.1)."""

import sqlite3
from pathlib import Path

import pytest

from thingwire.audit_log import AuditLog
from thingwire.safety_audit_chain import (
    CHAIN_BREAK,
    HEAD_MISMATCH,
    ROW_HASH_MISMATCH,
    UNCHAINED_AFTER_CHAINED,
    main,
    verify_db,
)


async def filled(path: Path, rows: int = 5) -> AuditLog:
    log = AuditLog(str(path))
    await log.initialize()
    for i in range(rows):
        await log.record("bot", "do_nod", {"times": i}, {"ok": True}, decision="allow")
    return log


def tamper(path: Path, sql: str, *args: object) -> None:
    with sqlite3.connect(path) as db:
        db.execute(sql, args)


async def test_untouched_chain_verifies_and_head_matches(tmp_path: Path) -> None:
    path = tmp_path / "a.db"
    log = await filled(path)
    head = await log.head_hash()
    await log.close()
    result = verify_db(str(path), head)
    assert result.ok and result.checked == 5 and result.head == head


async def test_edited_row_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "a.db"
    await (await filled(path)).close()
    tamper(path, "UPDATE commands SET decision = 'allow', reason = 'OK' WHERE id = 3")
    tamper(path, "UPDATE commands SET params_json = '{\"times\": 99}' WHERE id = 3")
    result = verify_db(str(path))
    assert not result.ok and result.first_bad_id == 3 and result.reason == ROW_HASH_MISMATCH


async def test_deleted_middle_row_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "a.db"
    await (await filled(path)).close()
    tamper(path, "DELETE FROM commands WHERE id = 3")
    result = verify_db(str(path))
    assert not result.ok and result.first_bad_id == 4 and result.reason == CHAIN_BREAK


async def test_row_rehashed_in_place_still_breaks_the_next_link(tmp_path: Path) -> None:
    from thingwire.safety_audit_chain import HASH_COLUMNS, compute_row_hash

    path = tmp_path / "a.db"
    await (await filled(path)).close()
    tamper(path, "UPDATE commands SET reason = 'edited' WHERE id = 2")
    with sqlite3.connect(path) as db:
        cols = ", ".join(HASH_COLUMNS)
        row = db.execute(f"SELECT prev_hash, {cols} FROM commands WHERE id = 2").fetchone()
        db.execute(
            "UPDATE commands SET row_hash = ? WHERE id = 2", (compute_row_hash(row[0], row[1:]),)
        )
    result = verify_db(str(path))
    assert not result.ok and result.first_bad_id == 3 and result.reason == CHAIN_BREAK


async def test_truncated_tail_needs_a_saved_head(tmp_path: Path) -> None:
    path = tmp_path / "a.db"
    log = await filled(path)
    head = await log.head_hash()
    await log.close()
    tamper(path, "DELETE FROM commands WHERE id > 3")
    assert verify_db(str(path)).ok
    result = verify_db(str(path), head)
    assert not result.ok and result.reason == HEAD_MISMATCH


async def test_rotation_keeps_the_rest_verifiable(tmp_path: Path) -> None:
    path = tmp_path / "a.db"
    await (await filled(path)).close()
    tamper(path, "DELETE FROM commands WHERE id <= 2")
    result = verify_db(str(path))
    assert result.ok and result.checked == 3


async def test_legacy_rows_before_the_chain_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "a.db"
    await (await filled(path, 3)).close()
    tamper(path, "UPDATE commands SET prev_hash = NULL, row_hash = NULL WHERE id = 1")
    result = verify_db(str(path))
    assert result.ok and result.checked == 2


async def test_unchained_row_after_the_chain_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "a.db"
    await (await filled(path, 3)).close()
    tamper(path, "UPDATE commands SET prev_hash = NULL, row_hash = NULL WHERE id = 3")
    result = verify_db(str(path))
    assert not result.ok and result.first_bad_id == 3
    assert result.reason == UNCHAINED_AFTER_CHAINED


async def test_chain_survives_reopen_and_concurrent_writes(tmp_path: Path) -> None:
    import asyncio

    path = tmp_path / "a.db"
    log = await filled(path, 2)
    await log.close()
    log = AuditLog(str(path))
    await log.initialize()
    await asyncio.gather(*(log.record("bot", "do_nod", {"i": i}, {}) for i in range(20)))
    await log.close()
    result = verify_db(str(path))
    assert result.ok and result.checked == 22


@pytest.mark.parametrize(("tampered", "code"), [(False, 0), (True, 1)])
async def test_cli_exit_codes(tmp_path: Path, tampered: bool, code: int) -> None:
    path = tmp_path / "a.db"
    await (await filled(path)).close()
    if tampered:
        tamper(path, "UPDATE commands SET action = 'x' WHERE id = 2")
    assert main([str(path)]) == code


def test_cli_usage_error_on_missing_database(tmp_path: Path) -> None:
    assert main([str(tmp_path / "missing.db")]) == 2
