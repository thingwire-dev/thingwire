"""Audit log — SQLite-based command audit trail.

Every tool call is recorded with timestamp, device, action, params, and result.
Uses aiosqlite for async operations. WAL mode for concurrent reads during writes.
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite

from thingwire.safety_motion import Decision

logger = logging.getLogger(__name__)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS commands (
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

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_commands_device_ts
ON commands (device_id, timestamp DESC)
"""

IDENTITY_COLUMNS = {
    "agent_id": "TEXT",
    "delegation_chain": "TEXT",
    "scopes": "TEXT",
    "decision": "TEXT",
    "reason": "TEXT",
}


class AuditLog:
    """Async SQLite audit log for command tracking."""

    def __init__(self, db_path: str, retention_days: int = 30) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self._retention_days = retention_days

    async def initialize(self) -> None:
        """Open database, enable WAL mode, create tables, run rotation."""
        try:
            self._db = await aiosqlite.connect(self._db_path)
        except Exception as e:
            logger.error("Failed to open audit log at %s: %s", self._db_path, e)
            raise

        # WAL mode for concurrent reads during writes
        if self._db_path != ":memory:":
            await self._db.execute("PRAGMA journal_mode=WAL")

        await self._db.execute(CREATE_TABLE_SQL)
        await self._db.execute(CREATE_INDEX_SQL)
        await self._migrate()
        await self._db.commit()

        # Rotate old entries on startup
        deleted = await self._rotate()
        if deleted > 0:
            logger.info(
                "Audit log rotated: deleted %d entries older than %d days",
                deleted,
                self._retention_days,
            )

        logger.info("Audit log initialized at %s", self._db_path)

    async def _migrate(self) -> None:
        """Idempotently add identity and decision columns to older databases."""
        assert self._db is not None
        cursor = await self._db.execute("PRAGMA table_info(commands)")
        existing = {row[1] for row in await cursor.fetchall()}
        for name, sql_type in IDENTITY_COLUMNS.items():
            if name not in existing:
                await self._db.execute(f"ALTER TABLE commands ADD COLUMN {name} {sql_type}")

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            try:
                await self._db.close()
            except Exception:
                logger.exception("Error closing audit log database")
            finally:
                self._db = None

    async def record(
        self,
        device_id: str,
        action: str,
        params: dict[str, Any],
        result: dict[str, Any],
        confirmed: bool = False,
        source: str = "mcp",
        agent_id: str | None = None,
        delegation_chain: list[str] | None = None,
        scopes: list[str] | None = None,
        decision: str | None = None,
        reason: str | None = None,
    ) -> int:
        """Record a command execution to the audit log. Returns row ID."""
        if not self._db:
            logger.warning("Audit log not initialized, dropping entry: %s/%s", device_id, action)
            return 0

        timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        try:
            cursor = await self._db.execute(
                """
                INSERT INTO commands
                (timestamp, device_id, action, params_json, result_json, confirmed, source,
                 agent_id, delegation_chain, scopes, decision, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    device_id,
                    action,
                    json.dumps(params),
                    json.dumps(result),
                    int(confirmed),
                    source,
                    agent_id,
                    json.dumps(delegation_chain) if delegation_chain is not None else None,
                    json.dumps(scopes) if scopes is not None else None,
                    decision,
                    reason,
                ),
            )
            await self._db.commit()
            row_id = cursor.lastrowid or 0
            logger.debug("Audit log entry #%d: %s/%s", row_id, device_id, action)
            return row_id
        except Exception:
            logger.exception("Failed to write audit log entry for %s/%s", device_id, action)
            return 0

    async def record_decision(
        self,
        device_id: str,
        action: str,
        params: dict[str, Any],
        verdict: Decision,
        *,
        confirmed: bool = False,
        source: str = "mcp",
        agent_id: str | None = None,
        delegation_chain: list[str] | None = None,
        scopes: list[str] | None = None,
    ) -> int:
        """Record a safety Decision (verdict, reason code, message, details)."""
        return await self.record(
            device_id,
            action,
            params,
            {"allowed": verdict.allowed, "message": verdict.message, "details": verdict.details},
            confirmed=confirmed,
            source=source,
            agent_id=agent_id,
            delegation_chain=delegation_chain,
            scopes=scopes,
            decision=verdict.decision,
            reason=verdict.code,
        )

    async def get_recent(
        self,
        device_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Query recent audit log entries."""
        if not self._db:
            logger.warning("Audit log not initialized")
            return []

        try:
            if device_id:
                cursor = await self._db.execute(
                    """
                    SELECT id, timestamp, device_id, action,
                        params_json, result_json, confirmed, source,
                        agent_id, delegation_chain, scopes, decision, reason
                    FROM commands
                    WHERE device_id = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (device_id, limit),
                )
            else:
                cursor = await self._db.execute(
                    """
                    SELECT id, timestamp, device_id, action,
                        params_json, result_json, confirmed, source,
                        agent_id, delegation_chain, scopes, decision, reason
                    FROM commands
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (limit,),
                )

            rows = await cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "timestamp": row[1],
                    "device_id": row[2],
                    "action": row[3],
                    "params": json.loads(row[4]) if row[4] else None,
                    "result": json.loads(row[5]) if row[5] else None,
                    "confirmed": bool(row[6]),
                    "source": row[7],
                    "agent_id": row[8],
                    "delegation_chain": json.loads(row[9]) if row[9] else None,
                    "scopes": json.loads(row[10]) if row[10] else None,
                    "decision": row[11],
                    "reason": row[12],
                }
                for row in rows
            ]
        except Exception:
            logger.exception("Failed to query audit log")
            return []

    async def _rotate(self) -> int:
        """Delete entries older than retention_days. Returns count deleted."""
        if not self._db or self._retention_days <= 0:
            return 0

        cutoff_dt = datetime.now(UTC) - timedelta(days=self._retention_days)
        cutoff = cutoff_dt.isoformat().replace("+00:00", "Z")
        try:
            cursor = await self._db.execute(
                "DELETE FROM commands WHERE timestamp < ?",
                (cutoff,),
            )
            await self._db.commit()
            return cursor.rowcount
        except Exception:
            logger.exception("Failed to rotate audit log")
            return 0
