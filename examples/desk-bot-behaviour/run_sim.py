#!/usr/bin/env python3
"""Run the desk bot behaviour loop in simulation and print a timeline.

    python examples/desk-bot-behaviour/run_sim.py [--seed 7] [--seconds 300] [--mcap out.mcap]

No hardware, no network. The PIR is scripted; time is simulated, so this finishes at once.
Set the THINGWIRE_* variables described in the README to try the optional LLM chooser.
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from beh_llm import llm_from_env  # noqa: E402
from beh_sim import DEFAULT_DURATION, DEFAULT_SCHEDULE, build_sim  # noqa: E402

from thingwire.recorder import Recorder  # noqa: E402


def render(timeline_rows: list[tuple[float, str, str, str]]) -> str:
    return "\n".join(f"{t:7.2f}s  {s:<8} {k:<7} {d}" for t, s, k, d in timeline_rows)


async def main_async(seed: int, seconds: float, mcap: Path | None, use_llm: bool) -> int:
    chooser = llm_from_env(os.environ) if use_llm else None
    if use_llm and chooser is None:
        print("LLM chooser not enabled (see README); using the rule-based chooser.")
    sim = build_sim(seed, chooser=chooser)
    recorder = Recorder(mcap) if mcap else None
    if recorder:
        sim.arm.attach_recorder(recorder)
    try:
        timeline = await sim.run(seconds)
    finally:
        if recorder:
            recorder.close()
    rows = [(e.t, e.state, e.kind, e.detail) for e in timeline]
    print(render(rows))
    acts = sum(e.kind == "act" for e in timeline)
    denied = sum(e.kind == "denied" for e in timeline)
    print(f"\n{len(timeline)} events, {acts} commands accepted, {denied} denied by safety")
    print("PIR high at:", ", ".join(f"{lo:g}-{hi:g}s" for lo, hi in DEFAULT_SCHEDULE))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--seconds", type=float, default=DEFAULT_DURATION)
    parser.add_argument("--mcap", type=Path, default=None)
    parser.add_argument("--llm", action="store_true", help="use the LLM chooser if enabled by env")
    args = parser.parse_args()
    logging.getLogger("thingwire").setLevel(logging.CRITICAL)  # denials are expected
    return asyncio.run(main_async(args.seed, args.seconds, args.mcap, args.llm))


if __name__ == "__main__":
    raise SystemExit(main())
