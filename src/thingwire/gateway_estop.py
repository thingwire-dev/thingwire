"""Human e-stop reset path: the CLI files a request, the running gateway applies it.

Resetting is never an MCP tool. `thingwire estop reset`, run on the gateway host, writes
a request row into the safety state DB. The gateway picks it up on its next safety sweep
and calls `SafetyLayer.reset_estop`, which checks the authorized resetter list. Requests
older than the TTL expire unapplied, so a forgotten request cannot clear a later trip.
Write access to the state DB file is the trust boundary; the gateway keeps it mode 0600.
"""

import logging
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing

from thingwire.safety import SafetyLayer
from thingwire.safety_codes import SafetyError
from thingwire.safety_estop import GLOBAL_SCOPE

logger = logging.getLogger(__name__)

WallClock = Callable[[], float]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS estop_reset_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    who TEXT NOT NULL,
    reason TEXT NOT NULL,
    requested_at REAL NOT NULL,
    outcome TEXT,
    handled_at REAL
)
"""


def _connect(db_path: str) -> sqlite3.Connection:
    db = sqlite3.connect(db_path, timeout=5)
    db.execute(_SCHEMA)
    return db


def request_reset(
    db_path: str, device_id: str | None, who: str, reason: str, clock: WallClock = time.time
) -> int:
    """File a reset request (device id, or None for the global latch). Returns its id."""
    scope = GLOBAL_SCOPE if device_id is None else device_id
    with closing(_connect(db_path)) as db:
        cur = db.execute(
            "INSERT INTO estop_reset_requests (scope, who, reason, requested_at) "
            "VALUES (?, ?, ?, ?)",
            (scope, who, reason, clock()),
        )
        db.commit()
        return int(cur.lastrowid or 0)


def request_outcome(db_path: str, request_id: int) -> str | None:
    """Outcome of a request once the gateway handled it, else None."""
    with closing(_connect(db_path)) as db:
        row = db.execute(
            "SELECT outcome FROM estop_reset_requests WHERE id = ?", (request_id,)
        ).fetchone()
    return None if row is None or row[0] is None else str(row[0])


def latched_scopes(db_path: str) -> list[tuple[str, str, str, str]]:
    """(scope, reason, source, tripped_at) for every persisted latch."""
    try:
        with closing(sqlite3.connect(db_path, timeout=5)) as db:
            rows = db.execute(
                "SELECT scope, reason, source, tripped_at FROM estop_latch ORDER BY scope"
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [(str(a), str(b), str(c), str(d)) for a, b, c, d in rows]


class ResetInbox:
    """Applies pending reset requests to a SafetyLayer (called from the safety sweep)."""

    def __init__(self, db_path: str, ttl_seconds: float, clock: WallClock = time.time) -> None:
        self._db_path = db_path
        self._ttl = ttl_seconds
        self._clock = clock

    def process(self, safety: SafetyLayer) -> list[tuple[int, str]]:
        """Handle every pending request. Returns (request id, outcome) pairs."""
        handled: list[tuple[int, str]] = []
        with closing(_connect(self._db_path)) as db:
            rows = db.execute(
                "SELECT id, scope, who, reason, requested_at FROM estop_reset_requests "
                "WHERE outcome IS NULL ORDER BY id"
            ).fetchall()
            for req_id, scope, who, reason, requested_at in rows:
                outcome = self._apply(safety, str(scope), str(who), str(reason), requested_at)
                db.execute(
                    "UPDATE estop_reset_requests SET outcome = ?, handled_at = ? WHERE id = ?",
                    (outcome, self._clock(), req_id),
                )
                db.commit()
                logger.warning("E-stop reset request %d by %s: %s", req_id, who, outcome)
                handled.append((int(req_id), outcome))
        return handled

    def _apply(
        self, safety: SafetyLayer, scope: str, who: str, reason: str, requested_at: float
    ) -> str:
        if self._clock() - float(requested_at) > self._ttl:
            return "expired"
        device_id = None if scope == GLOBAL_SCOPE else scope
        try:
            cleared = safety.reset_estop(device_id, reason, who)
        except SafetyError as exc:
            return f"refused:{exc.code}"
        except Exception:
            logger.exception("E-stop reset failed for %s", scope)
            return "refused:SAFETY_CHECK_ERROR"
        return "reset" if cleared else "not_latched"
