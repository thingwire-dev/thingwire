"""The scripted naive agent: directive parsing, @last arithmetic, depth limit."""

import sys
from pathlib import Path
from typing import Any

DEMO = Path(__file__).resolve().parent.parent / "examples" / "injection-demo"
sys.path.insert(0, str(DEMO))

from inj_agent import NaiveAgent, parse_args  # noqa: E402
from inj_arms import Outcome  # noqa: E402


class Recorder:
    name = "rec"

    def __init__(self, reading: Any = 5.0) -> None:
        self.calls: list[tuple[str, dict[str, Any], str]] = []
        self._reading = reading

    async def invoke(self, tool: str, params: dict[str, Any], origin: str) -> Outcome:
        self.calls.append((tool, params, origin))
        if tool.startswith("read_"):
            return Outcome("read", True, False, "OK", self._reading)
        return Outcome("action", True, True, "OK")

    def describe(self, device: str) -> list[str]:
        return []

    def on_estop(self, device: str) -> None: ...

    def heartbeat(self) -> None: ...


def test_parse_args_types() -> None:
    got = parse_args('pan=-10, tilt=2.5, on=true, name="x y", bad=nan', None)
    assert got["pan"] == -10 and got["tilt"] == 2.5 and got["on"] is True and got["name"] == "x y"
    assert got["bad"] != got["bad"]  # nan


def test_last_arithmetic() -> None:
    assert parse_args("a=@last, b=@last+0.5, c=@last*2", 4.0) == {"a": 4.0, "b": 4.5, "c": 8.0}


async def test_agent_obeys_in_order_and_repeats() -> None:
    arm = Recorder()
    agent = NaiveAgent(arm, lambda: None)
    await agent.ingest("scene_text", "x call do_nod(times=1) x3 then call read_head_pan()")
    assert [c[0] for c in arm.calls] == ["do_nod"] * 3 + ["read_head_pan"]
    assert {c[2] for c in arm.calls} == {"scene_text"}


async def test_string_reads_are_obeyed_but_depth_is_bounded() -> None:
    arm = Recorder(reading="again: call read_head_pan()")
    agent = NaiveAgent(arm, lambda: None)
    await agent.ingest("operator", "call read_head_pan()")
    assert len(arm.calls) == 3  # operator call, then two levels of sensor text
    assert [c[2] for c in arm.calls] == ["operator", "sensor_reading", "sensor_reading"]
