"""Tamper evident hash chain for the audit log (contracts 4.1).

Each chained row stores the hash of the previous chained row and its own hash over the stored
fields. Editing, deleting, inserting or reordering a row breaks the chain. This is not a
signature: whoever can rewrite the whole database can recompute every hash, and truncating the
newest rows is only detectable against a saved head hash.
"""

import argparse
import hashlib
import json
import sqlite3
import sys
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

HASH_COLUMNS = (
    "timestamp",
    "device_id",
    "action",
    "params_json",
    "result_json",
    "confirmed",
    "source",
    "agent_id",
    "delegation_chain",
    "scopes",
    "decision",
    "reason",
)

ROW_HASH_MISMATCH = "row_hash_mismatch"
CHAIN_BREAK = "chain_break"
UNCHAINED_AFTER_CHAINED = "unchained_after_chained"
HEAD_MISMATCH = "head_mismatch"


def compute_row_hash(prev_hash: str | None, fields: Sequence[Any]) -> str:
    """SHA-256 over prev_hash, a newline and the compact JSON array of the stored fields."""
    body = json.dumps(list(fields), separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(f"{prev_hash or ''}\n{body}".encode()).hexdigest()


@dataclass(frozen=True)
class ChainResult:
    """Outcome of a verification run."""

    ok: bool
    checked: int
    head: str | None
    first_bad_id: int | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def verify_rows(rows: Iterable[tuple[Any, ...]], expected_head: str | None = None) -> ChainResult:
    """Verify rows of (id, prev_hash, row_hash, *HASH_COLUMNS values) ordered by id.

    The first chained row's prev_hash is the anchor and is trusted as stored, because
    retention rotation deletes old rows.
    """
    checked, head, chained = 0, None, False
    for row in rows:
        row_id, prev, stored = row[0], row[1], row[2]
        if stored is None:
            if chained:
                return ChainResult(False, checked, head, row_id, UNCHAINED_AFTER_CHAINED)
            continue
        if compute_row_hash(prev, row[3:]) != stored:
            return ChainResult(False, checked, head, row_id, ROW_HASH_MISMATCH)
        if chained and prev != head:
            return ChainResult(False, checked, head, row_id, CHAIN_BREAK)
        chained, head, checked = True, stored, checked + 1
    if expected_head is not None and expected_head != head:
        return ChainResult(False, checked, head, None, HEAD_MISMATCH)
    return ChainResult(True, checked, head)


def verify_db(path: str, expected_head: str | None = None) -> ChainResult:
    """Open a SQLite audit database read only and verify its chain."""
    columns = ", ".join(HASH_COLUMNS)
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        rows = db.execute(
            f"SELECT id, prev_hash, row_hash, {columns} FROM commands ORDER BY id"
        ).fetchall()
    return verify_rows(rows, expected_head)


def main(argv: list[str] | None = None) -> int:
    """Exit 0 when the chain is intact, 1 when it is not, 2 on a usage or open error."""
    parser = argparse.ArgumentParser(description="Verify the ThingWire audit log hash chain")
    parser.add_argument("db", help="path to the audit SQLite database")
    parser.add_argument("--head", help="expected row_hash of the newest row, saved earlier")
    args = parser.parse_args(argv)
    try:
        result = verify_db(args.db, args.head)
    except sqlite3.Error as exc:
        print(f"cannot read audit log: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.as_dict()))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
