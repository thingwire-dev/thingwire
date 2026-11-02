"""What a reading or an argument offers to taint matching: numbers and string digests.

Numbers inside strings and structured values are pulled out by a regex; strings are also
reduced to a digest of their stripped, casefolded text. Both are bounded so a hostile reading
cannot make the ledger expensive. Pure functions, no state (contracts 5.5).
"""

import math
import re
from collections import deque
from dataclasses import dataclass
from typing import Any

from thingwire.provenance import content_hash

NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Features:
    """Numbers and string digests found in one value."""

    numbers: frozenset[float] = frozenset()
    digests: frozenset[str] = frozenset()

    def __bool__(self) -> bool:
        return bool(self.numbers or self.digests)


def string_digest(text: str) -> str | None:
    """Stable digest of a string ignoring case and surrounding space; None when empty."""
    norm = text.strip().casefold()
    return content_hash(norm) if norm else None


def extract(
    value: Any, *, max_leaves: int, max_chars: int, numbers_in_strings: bool = True
) -> Features:
    """Numbers and digests of every scalar leaf, up to `max_leaves` leaves in walk order."""
    numbers: set[float] = set()
    digests: set[str] = set()
    stack: deque[Any] = deque([value])
    leaves = 0
    while stack and leaves < max_leaves:
        item = stack.popleft()
        if isinstance(item, dict):
            stack.extendleft(reversed(list(item.values())))
        elif isinstance(item, list | tuple):
            stack.extendleft(reversed(item))
        elif isinstance(item, bool) or item is None:
            leaves += 1
        elif isinstance(item, int | float):
            leaves += 1
            if math.isfinite(item):
                numbers.add(float(item))
        elif isinstance(item, str):
            leaves += 1
            text = item[:max_chars]
            digest = string_digest(text)
            if digest is not None:
                digests.add(digest)
            if numbers_in_strings:
                numbers.update(float(m) for m in NUMBER_RE.findall(text) if math.isfinite(float(m)))
    return Features(frozenset(numbers), frozenset(digests))


def linked(reading: Features, argument: Features, tolerance: float) -> bool:
    """True if a digest is shared or a number of the argument is within `tolerance`."""
    if reading.digests & argument.digests:
        return True
    return any(abs(a - r) <= tolerance for a in argument.numbers for r in reading.numbers)
