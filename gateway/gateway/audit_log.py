"""Audit log — SQLite-based command audit trail.

Every tool call is recorded with timestamp, device, action, params, and result.
Uses aiosqlite for async operations.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

import aiosqlite

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


class AuditLog:
    """Async SQLite audit log for command tracking."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Open database and create tables."""
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute(CREATE_TABLE_SQL)
        await self._db.execute(CREATE_INDEX_SQL)
        await self._db.commit()
        logger.info("Audit log initialized at %s", self._db_path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    async def record(
        self,
        device_id: str,
        action: str,
        params: dict[str, Any],
        result: dict[str, Any],
        confirmed: bool = False,
        source: str = "mcp",
    ) -> int:
        """Record a command execution to the audit log. Returns row ID."""
        if not self._db:
            msg = "Audit log not initialized"
            raise RuntimeError(msg)

        timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        cursor = await self._db.execute(
            """
            INSERT INTO commands (timestamp, device_id, action, params_json, result_json, confirmed, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                device_id,
                action,
                json.dumps(params),
                json.dumps(result),
                int(confirmed),
                source,
            ),
        )
        await self._db.commit()
        row_id = cursor.lastrowid or 0
        logger.debug("Audit log entry #%d: %s/%s", row_id, device_id, action)
        return row_id

    async def get_recent(
        self,
        device_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Query recent audit log entries."""
        if not self._db:
            msg = "Audit log not initialized"
            raise RuntimeError(msg)

        if device_id:
            cursor = await self._db.execute(
                """
                SELECT id, timestamp, device_id, action, params_json, result_json, confirmed, source
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
                SELECT id, timestamp, device_id, action, params_json, result_json, confirmed, source
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
            }
            for row in rows
        ]
