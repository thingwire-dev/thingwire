"""Latched emergency stop with SQLite persistence.

Scope is a device id or GLOBAL_SCOPE. Latches survive restart; if persisted
state cannot be read the latch fails closed to a global estop.
"""

import logging
import sqlite3
import threading
from collections.abc import Callable, Iterable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime

from thingwire.safety_codes import DecisionCode, SafetyError

logger = logging.getLogger(__name__)

GLOBAL_SCOPE = "*"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS estop_latch (
    scope TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    source TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    tripped_at TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class LatchRecord:
    """One latched emergency stop."""

    scope: str
    reason: str
    source: str
    trace_id: str
    tripped_at: str


def _utc_now() -> datetime:
    return datetime.now(UTC)


class EstopLatch:
    """Thread-safe latch store. Only an authorized reset clears a latch."""

    def __init__(
        self,
        db_path: str | None = None,
        authorized_resetters: Iterable[str] = (),
        wall_clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._db_path = db_path
        self._authorized = frozenset(authorized_resetters)
        self._wall = wall_clock
        self._lock = threading.RLock()
        self._latched: dict[str, LatchRecord] = {}
        if not self._authorized:
            logger.warning("No authorized e-stop resetters configured; resets will be refused")
        self._load()

    def _stamp(self) -> str:
        return self._wall().isoformat().replace("+00:00", "Z")

    def _load(self) -> None:
        if self._db_path is None:
            return
        try:
            with closing(sqlite3.connect(self._db_path, timeout=5)) as db:
                db.execute(_SCHEMA)
                db.commit()
                rows = db.execute(
                    "SELECT scope, reason, source, trace_id, tripped_at FROM estop_latch"
                ).fetchall()
            self._latched = {r[0]: LatchRecord(*r) for r in rows}
        except sqlite3.Error:
            logger.exception("Cannot load e-stop state, failing closed to global estop")
            self._latched[GLOBAL_SCOPE] = LatchRecord(
                GLOBAL_SCOPE,
                "e-stop state unreadable at startup",
                DecisionCode.ESTOP_STATE_UNAVAILABLE,
                "startup",
                self._stamp(),
            )

    def _persist(self, sql: str, args: tuple[str, ...]) -> None:
        if self._db_path is None:
            return
        with closing(sqlite3.connect(self._db_path, timeout=5)) as db:
            db.execute(_SCHEMA)
            db.execute(sql, args)
            db.commit()

    def trip(self, scope: str, reason: str, source: str, trace_id: str) -> LatchRecord | None:
        """Latch scope. Returns the new record, or None if it was already latched."""
        with self._lock:
            if scope in self._latched:
                return None
            rec = LatchRecord(scope, reason, source, trace_id, self._stamp())
            self._latched[scope] = rec  # latch in memory first: fail closed
            try:
                self._persist(
                    "INSERT OR REPLACE INTO estop_latch VALUES (?, ?, ?, ?, ?)",
                    (rec.scope, rec.reason, rec.source, rec.trace_id, rec.tripped_at),
                )
            except sqlite3.Error:
                logger.exception("Could not persist e-stop for %s; latched in memory only", scope)
            return rec

    def reset(self, scope: str, who: str, reason: str) -> LatchRecord | None:
        """Clear a latch. Raises SafetyError if unauthorized or not persisted."""
        if not who or not reason.strip() or who not in self._authorized:
            raise SafetyError(
                DecisionCode.RESET_NOT_AUTHORIZED,
                "E-stop reset requires an authorized identity and a reason",
            )
        with self._lock:
            rec = self._latched.get(scope)
            if rec is None:
                return None
            try:
                self._persist("DELETE FROM estop_latch WHERE scope = ?", (scope,))
            except sqlite3.Error as exc:
                raise SafetyError(
                    DecisionCode.ESTOP_STATE_UNAVAILABLE,
                    "Could not persist e-stop reset; latch kept",
                ) from exc
            del self._latched[scope]
            return rec

    def latched_for(self, device_id: str) -> LatchRecord | None:
        """Global latch wins, then the device latch."""
        with self._lock:
            return self._latched.get(GLOBAL_SCOPE) or self._latched.get(device_id)

    def records(self) -> list[LatchRecord]:
        """Snapshot of all latches."""
        with self._lock:
            return list(self._latched.values())
