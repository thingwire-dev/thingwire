"""Actuator backed by the injection benchmark's gateway arm: the real ActionPipeline.

The desk bot gets only the scopes it needs (look_at, set_expression, nod). Nothing here
skips a check: the safety layer, identity gate and audit all run on every command.
"""

import sys
from pathlib import Path
from typing import Any

INJ = Path(__file__).resolve().parents[1] / "injection-demo"
if str(INJ) not in sys.path:
    sys.path.insert(0, str(INJ))

from beh_machine import ActResult  # noqa: E402
from inj_arms import Registry  # noqa: E402
from inj_gateway import GatewayArm  # noqa: E402
from inj_tds import DESK, desk_td  # noqa: E402
from inj_world import DeviceAdapter, IdSource, SimClock  # noqa: E402

from thingwire.recorder import Recorder  # noqa: E402

AGENT_SCOPES = tuple(f"device:{DESK}:act:{a}" for a in ("look_at", "set_expression", "nod"))
TOOLS = {"look_at": "do_look_at", "set_expression": "do_set_expression", "nod": "do_nod"}


def desk_expressions() -> tuple[str, ...]:
    """The expression enum straight from the TD, so the personality is checked against it."""
    enum = desk_td()["properties"]["expression"]["enum"]
    return tuple(str(e) for e in enum)


class BehaviourArm(GatewayArm):
    """The benchmark's gateway arm, with an optional MCAP recorder on its pipeline."""

    def attach_recorder(self, recorder: Recorder) -> None:
        self._pipe.recorder = recorder


class GatewayActuator:
    """Maps loop commands to tool calls on the gateway. Unknown actions are refused."""

    def __init__(self, arm: BehaviourArm) -> None:
        self._arm = arm

    async def act(self, action: str, params: dict[str, Any]) -> ActResult:
        tool = TOOLS.get(action)
        if tool is None:
            return ActResult(False, "UNKNOWN_ACTION")
        out = await self._arm.invoke(tool, params, "operator")
        return ActResult(out.delivered, out.code)

    def heartbeat(self) -> None:
        self._arm.heartbeat()

    def estopped(self) -> bool:
        return self._arm.safety.estop_latched(DESK)


def build_world(seed: int) -> tuple[SimClock, DeviceAdapter, BehaviourArm]:
    clock = SimClock()
    adapter = DeviceAdapter(clock)
    arm = BehaviourArm(
        Registry({DESK: desk_td()}), adapter, clock, IdSource(seed), AGENT_SCOPES, "absent"
    )
    return clock, adapter, arm
