#!/usr/bin/env python3
"""Run the prompt-injection benchmark: baseline (no safety layer) versus the gateway.

    python examples/injection-demo/run_benchmark.py [--seed 7] [--out DIR]

No model, no network. Output is identical on every run for the same seed.
"""

import argparse
import logging
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from inj_report import gaps, table, write  # noqa: E402
from inj_run import run_all  # noqa: E402

DEFAULT_SEED = 7


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=HERE / "results")
    args = parser.parse_args()
    logging.getLogger("thingwire").setLevel(logging.CRITICAL)  # denials are expected here
    result = run_all(args.seed)
    write(result, args.out)
    print(table(result))
    print(f"\nGateway did not block {len(gaps(result))} case(s); see {args.out / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
