"""The full corpus plus a schema check used by the runner and the tests."""

from inj_benign import BENIGN
from inj_corpus_a import PART_A
from inj_corpus_b import PART_B
from inj_model import CATEGORIES, CHANNELS, Benign, Case

CASES: tuple[Case, ...] = PART_A + PART_B


def validate(cases: tuple[Case, ...] = CASES, benign: tuple[Benign, ...] = BENIGN) -> list[str]:
    """Problems with the corpus, empty when it is well formed."""
    problems: list[str] = []
    ids = [c.id for c in cases] + [b.id for b in benign]
    problems += [f"duplicate id {i}" for i in sorted(set(ids)) if ids.count(i) > 1]
    for c in cases:
        if c.category not in CATEGORIES:
            problems.append(f"{c.id}: unknown category {c.category}")
        if c.channel not in CHANNELS:
            problems.append(f"{c.id}: unknown channel {c.channel}")
        if not c.expected_safe or not c.expected_codes or not c.events:
            problems.append(f"{c.id}: missing expected outcome or events")
    problems += [
        f"no case in category {k}" for k in CATEGORIES if not any(c.category == k for c in cases)
    ]
    return problems
