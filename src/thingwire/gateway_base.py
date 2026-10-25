"""Shared types and outputs (audit log, MCAP) for the gateway tool pipeline."""

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from thingwire.gateway_taint import TaintTracker
from thingwire.identity import AgentIdentity
from thingwire.identity_gate import Verdict
from thingwire.recorder import Recorder
from thingwire.safety import SafetyLayer
from thingwire.safety_motion import Decision
from thingwire.tool_compiler import CompiledTool

logger = logging.getLogger(__name__)


class Bridge(Protocol):
    def is_device_online(self, device_id: str) -> bool: ...

    def get_latest_reading(self, device_id: str, property_name: str) -> dict[str, Any]: ...

    async def send_command(self, device_id: str, target: str, command: str, value: Any) -> str: ...


class Audit(Protocol):
    async def record(
        self,
        device_id: str,
        action: str,
        params: dict[str, Any],
        result: dict[str, Any],
        confirmed: bool = ...,
        *,
        agent_id: str | None = ...,
        delegation_chain: list[str] | None = ...,
        scopes: list[str] | None = ...,
        decision: str | None = ...,
        reason: str | None = ...,
    ) -> int: ...


@dataclass(frozen=True)
class CommandRoute:
    """MQTT command fields for one TD action (contracts.md 1.2)."""

    target: str
    command: str


def safety_action(tool: CompiledTool) -> str:
    """Name the safety layer allowlists for a TD action (`do_<TD action name>`)."""
    return f"do_{tool.source_name}"


def error_result(decision: Decision) -> dict[str, Any]:
    return {"error": decision.code, "message": decision.message, "details": decision.details}


@dataclass
class Call:
    tool: CompiledTool
    params: dict[str, Any]
    ident: AgentIdentity | None
    approved: bool = False
    verdict: Verdict | None = None  # gate verdict for the audit row; None without identity

    @property
    def device(self) -> str:
        return self.tool.device_id

    @property
    def action(self) -> str:
        return safety_action(self.tool)

    def who(self) -> dict[str, Any]:
        i = self.ident
        if i is None:
            return {"agent_id": None, "delegation_chain": None, "scopes": None}
        return {
            "agent_id": i.agent_id,
            "delegation_chain": list(i.delegation_chain),
            "scopes": sorted(i.scopes),
        }


class PipelineBase:
    """Collaborators and output helpers shared by the pipeline steps."""

    def __init__(
        self,
        bridge: Bridge,
        safety: SafetyLayer,
        audit: Audit,
        *,
        recorder: Recorder | None = None,
        taint: TaintTracker | None = None,
    ) -> None:
        self.bridge, self.safety, self.audit = bridge, safety, audit
        self.recorder, self.taint = recorder, taint

    async def _refuse(
        self, call: Call, stage: str, decision: Decision, result: dict[str, Any], reason: str
    ) -> dict[str, Any]:
        seq = self._record_call(call, stage)
        self._record_decision(seq, call, decision)
        await self._audit(call, result, "deny", reason)
        return result

    # --- outputs ---------------------------------------------------------

    def _record_call(self, call: Call, stage: str | None) -> int:
        if self.recorder is None:
            return -1
        return self.recorder.record_tool_call(
            call.device, call.action, call.params, approved=call.approved, stage=stage, **call.who()
        )

    def _record_decision(self, seq: int, call: Call, decision: Decision) -> None:
        if self.recorder is not None:
            self.recorder.record_decision(seq, call.device, call.action, decision, **call.who())

    async def _audit(
        self,
        call: Call,
        result: dict[str, Any],
        decision: str,
        reason: str,
        confirmed: bool = False,
        params: dict[str, Any] | None = None,
    ) -> None:
        try:
            await self.audit.record(
                device_id=call.device,
                action=call.tool.name,
                params=call.params if params is None else params,
                result=result,
                confirmed=confirmed,
                decision=decision,
                reason=str(reason),
                **call.who(),
            )
        except Exception:
            logger.exception("Audit write failed for %s/%s", call.device, call.tool.name)
