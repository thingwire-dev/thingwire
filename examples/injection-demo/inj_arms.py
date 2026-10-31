"""The two arms. Both expose the same tool names; they differ in what a call goes through.

Baseline: the tool call is turned into a device command and handed straight to the device
adapter. This bypass lives only in this example; src/ has no such switch.
Gateway: the call goes through the real ActionPipeline (identity, taint, approval,
SafetyLayer, audit), the same path the MCP server registers for every device tool.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from inj_tds import DESK, RELAY, RELAY_PREFIX
from inj_world import DeviceAdapter

from thingwire.gateway_base import CommandRoute
from thingwire.mcp_server import command_route
from thingwire.td_loader import parse_thing_description_dict
from thingwire.tool_compiler import CompiledTool, compile_tools


@dataclass(frozen=True)
class Outcome:
    """What an arm did with one tool call."""

    kind: str  # read, action, meta or unknown
    ok: bool
    delivered: bool
    code: str
    value: Any = None


@dataclass(frozen=True)
class Tool:
    compiled: CompiledTool
    route: CommandRoute


class Arm(Protocol):
    name: str

    async def invoke(self, tool: str, params: dict[str, Any], origin: str) -> Outcome: ...

    def describe(self, device: str) -> list[str]: ...

    def on_estop(self, device: str) -> None: ...

    def heartbeat(self) -> None: ...


class Registry:
    """Tools compiled from the published (possibly poisoned) TDs, keyed by tool name."""

    def __init__(self, tds: dict[str, dict[str, Any]]) -> None:
        self.tds = tds
        self.tools: dict[str, Tool] = {}
        for device, raw in tds.items():
            parsed = parse_thing_description_dict(raw)
            prefix = RELAY_PREFIX if device == RELAY else None
            for compiled in compile_tools(parsed, device_prefix=prefix):
                tool = compiled.model_copy(update={"device_id": device})
                route = (
                    command_route(parsed, tool.source_name)
                    if tool.tool_type == "action"
                    else CommandRoute(tool.source_name, tool.source_name)
                )
                self.tools[tool.name] = Tool(tool, route)

    def describe(self, device: str) -> list[str]:
        texts = [str(self.tds[device].get("description", ""))]
        texts += [
            t.compiled.description for t in self.tools.values() if t.compiled.device_id == device
        ]
        return texts


UNKNOWN = Outcome("unknown", False, False, "UNKNOWN_TOOL")


class BaselineArm:
    """No safety layer: whatever the agent asks for is sent to the device adapter."""

    name = "baseline"

    def __init__(self, registry: Registry, adapter: DeviceAdapter) -> None:
        self._reg, self._dev = registry, adapter

    async def invoke(self, tool: str, params: dict[str, Any], origin: str) -> Outcome:
        self._dev.origin = origin
        if tool == "emergency_stop":
            self._dev.trip_device_estop()
            return Outcome("meta", True, False, "NO_GATEWAY")
        entry = self._reg.tools.get(tool)
        if entry is None:
            return UNKNOWN
        c = entry.compiled
        if c.tool_type == "read":
            res = self._dev.get_latest_reading(c.device_id, c.source_name)
            return Outcome("read", True, False, "NO_GATEWAY", res["value"])
        value = {k: v for k, v in params.items() if v is not None}
        await self._dev.send_command(c.device_id, entry.route.target, entry.route.command, value)
        return Outcome("action", True, True, "NO_GATEWAY")

    def describe(self, device: str) -> list[str]:
        return self._reg.describe(device)

    def on_estop(self, device: str) -> None:
        """The baseline has no latch of its own; the device-side stop is tripped by the world."""

    def heartbeat(self) -> None:
        """No deadman in the baseline."""


__all__ = ["DESK", "Arm", "BaselineArm", "Outcome", "Registry", "Tool"]
