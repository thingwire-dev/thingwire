"""The one gateway path for device tools: identity, taint, approval, safety, send, audit, record.

Order for an action call, every step fails closed:
1. identity scope (theAuth token), when the server runs with identity
2. command freshness (issued_at window, nonce replay cache)
3. taint policy on sensor-derived numeric arguments
4. human approval through theAuth for dangerous or tainted calls; without identity
   such calls are denied (stdio dev mode cannot approve anything)
5. `SafetyLayer.evaluate`, with `approved` only from a real theAuth approval
6. publish `decision.params` (validated, possibly clamped) to the TD action's command

Every allow and deny is written to the audit log and, when recording, to MCAP.
"""

import asyncio
import logging
from typing import Any

from thingwire.gateway_base import (
    Audit,
    Bridge,
    Call,
    CommandRoute,
    PipelineBase,
    error_result,
    safety_action,
)
from thingwire.gateway_taint import LOCAL_SESSION
from thingwire.identity import AgentIdentity
from thingwire.identity_gate import Gatekeeper, deny
from thingwire.safety_codes import DecisionCode, SafetyError
from thingwire.safety_motion import Decision
from thingwire.taint_policy import CODE_REQUIRES_APPROVAL
from thingwire.tool_compiler import CompiledTool

logger = logging.getLogger(__name__)

CODE_MISSING_SCOPE = "MISSING_SCOPE"
CODE_IDENTITY_DENIED = "IDENTITY_DENIED"
CODE_APPROVAL_DENIED = "APPROVAL_DENIED"
CODE_DEVICE_OFFLINE = "DEVICE_OFFLINE"
CODE_COMMAND_FAILED = "COMMAND_FAILED"
MAX_STOP_REASON_CHARS = 200

__all__ = ["ActionPipeline", "Audit", "Bridge", "CommandRoute", "safety_action"]


class ActionPipeline(PipelineBase):
    """Runs device tool calls for one gateway."""

    # --- actions ---------------------------------------------------------

    async def execute(
        self,
        tool: CompiledTool,
        route: CommandRoute,
        params: dict[str, Any],
        ident: AgentIdentity | None,
        gate: Gatekeeper | None,
        *,
        issued_at: object = None,
        nonce: object = None,
    ) -> dict[str, Any]:
        """Run one action call through every check and, if allowed, send it.

        `issued_at` and `nonce` are the caller's freshness stamp (contracts 5.3). They are
        judged before taint and approval so a slow human does not age the command.
        """
        call = Call(tool, {k: v for k, v in params.items() if v is not None}, ident)
        dangerous = not tool.safe or self.safety.is_dangerous(call.device, call.action)
        if gate is not None:
            v = gate.check_scope(ident, call.device, tool.source_name, dangerous)
            if not v.allowed:
                code = CODE_MISSING_SCOPE if v.required_scope else CODE_IDENTITY_DENIED
                return await self._refuse(
                    call, "identity", Decision(False, code, v.reason), v.error(), v.reason
                )
            call.verdict = v
        stale = self.safety.check_freshness(call.device, call.action, issued_at, nonce)
        if stale is not None:
            return await self._refuse(call, "freshness", stale, error_result(stale), stale.code)
        taint = self._taint(call)
        if taint is not None and not taint.allowed and taint.code != CODE_REQUIRES_APPROVAL:
            return await self._refuse(call, "taint", taint, error_result(taint), taint.code)
        if dangerous or (taint is not None and not taint.allowed):
            why = "dangerous action" if dangerous else "action on untrusted sensor data"
            refused = await self._approve(call, gate, why)
            if refused is not None:
                return refused
        return await self._evaluate_and_send(call, route)

    def _taint(self, call: Call) -> Decision | None:
        if self.taint is None:
            return None
        session = call.ident.agent_id if call.ident else LOCAL_SESSION
        scopes = call.ident.scopes if call.ident else frozenset()
        return self.taint.evaluate(
            session, call.tool.source_name, call.params, granted_scopes=scopes
        )

    async def _approve(
        self, call: Call, gate: Gatekeeper | None, why: str
    ) -> dict[str, Any] | None:
        """Wait for a theAuth approval. Returns the refusal, or None once approved."""
        if gate is None or call.ident is None:
            d = Decision(
                False,
                DecisionCode.APPROVAL_REQUIRED,
                f"{why} needs a human approval through theAuth, and identity is not "
                "configured (stdio dev mode): denied",
            )
            return await self._refuse(call, "approval", d, error_result(d), d.code)
        pre = self._precheck(call)
        if pre is not None:  # do not ask a human to approve a call that cannot run
            return await self._refuse(call, "precheck", pre, error_result(pre), pre.code)
        v = await gate.wait_approval(call.ident, call.device, call.tool.source_name, call.params)
        if not v.allowed:
            d = Decision(False, CODE_APPROVAL_DENIED, v.reason)
            return await self._refuse(call, "approval", d, v.error(), v.reason)
        call.approved, call.verdict = True, v
        if not self.bridge.is_device_online(call.device):
            off = deny("device offline after approval")
            d = Decision(False, CODE_DEVICE_OFFLINE, off.reason)
            return await self._refuse(call, "approval", d, off.error(), off.reason)
        return None

    def _precheck(self, call: Call) -> Decision | None:
        """Non-mutating subset of the safety checks (latch, flood, allowlist, deadman)."""
        try:
            self.safety.guards.check_estop(call.device, call.action)
            self.safety.guards.check_flood(call.device, call.action)
            self.safety.check_permission(call.device, call.action)
            self.safety.check_deadman_switch(call.device)
        except SafetyError as exc:
            return Decision(False, exc.code, exc.message)
        except Exception:
            logger.exception("Safety precheck failed for %s/%s", call.device, call.action)
            return Decision(False, DecisionCode.SAFETY_CHECK_ERROR, "safety precheck failed")
        return None

    async def _evaluate_and_send(self, call: Call, route: CommandRoute) -> dict[str, Any]:
        seq = self._record_call(call, None)
        decision = self.safety.evaluate(
            call.device, call.action, call.params, approved=call.approved, **call.who()
        )
        self._record_decision(seq, call, decision)
        if not decision.allowed:
            result = error_result(decision)
            if call.approved:  # state changed while the human was deciding
                result = {
                    **deny(f"safety recheck failed: {decision.code}").error(),
                    "code": decision.code,
                    "message": decision.message,
                }
            await self._audit(call, result, "deny", decision.code)
            return result
        try:
            action_id = await self.bridge.send_command(
                device_id=call.device,
                target=route.target,
                command=route.command,
                value=decision.params,
            )
        except RuntimeError as exc:
            failed = {"error": CODE_COMMAND_FAILED, "message": str(exc), "device_id": call.device}
            await self._audit(call, failed, "allow", CODE_COMMAND_FAILED, call.approved)
            return failed
        result = {
            "status": "command_sent",
            "action_id": action_id,
            "device_id": call.device,
            "target": route.target,
            "command": route.command,
            "value": decision.params,
            "code": decision.code,
        }
        verdict = call.verdict.decision if call.verdict else decision.decision
        await self._audit(call, result, verdict, decision.code, call.approved)
        return result

    # --- reads and stop --------------------------------------------------

    async def read(
        self, tool: CompiledTool, ident: AgentIdentity | None, gate: Gatekeeper | None
    ) -> dict[str, Any]:
        """Read one property; the result carries its provenance (origin, taint_id)."""
        call = Call(tool, {}, ident)
        if gate is not None:
            v = gate.check_read(ident, tool.device_id)
            if not v.allowed:
                await self._audit(call, v.error(), "deny", v.reason)
                return v.error()
        if not self.bridge.is_device_online(tool.device_id):
            return {
                "error": "DEVICE_OFFLINE",
                "message": f"Device '{tool.device_id}' is offline. Cannot read {tool.source_name}.",
                "device_id": tool.device_id,
            }
        result = self.bridge.get_latest_reading(tool.device_id, tool.source_name)
        if self.taint is not None:
            session = ident.agent_id if ident else LOCAL_SESSION
            result = self.taint.observe_read(session, tool.device_id, tool.source_name, result)
        await self._audit(call, result, "allow", DecisionCode.OK)
        return result

    async def emergency_stop(
        self,
        device_id: str,
        reason: str,
        known: bool,
        ident: AgentIdentity | None,
        gate: Gatekeeper | None,
    ) -> dict[str, Any]:
        """Let an agent latch the e-stop for a device. Resetting is never an agent tool."""
        if gate is not None and not (v := gate.check_read(ident, device_id)).allowed:
            return v.error()
        if not known:
            return {"error": "DEVICE_NOT_REGISTERED", "message": f"Unknown device '{device_id}'"}
        who = ident.agent_id if ident else LOCAL_SESSION
        text = f"agent {who}: {reason.strip()[:MAX_STOP_REASON_CHARS] or 'stop requested'}"
        newly = await asyncio.to_thread(self.safety.trip_estop, device_id, text, "mcp")
        result = {
            "status": "estop_latched",
            "device_id": device_id,
            "newly_latched": newly,
            "message": "Only an authorized human can reset: thingwire estop reset",
        }
        call = Call(
            CompiledTool(
                name="emergency_stop",
                source_name="emergency_stop",
                device_id=device_id,
                description="",
                parameters=[],
                tool_type="action",
                safe=True,
                idempotent=True,
            ),
            {},
            ident,
        )
        await self._audit(
            call, result, "allow", DecisionCode.ESTOP_LATCHED, params={"reason": text}
        )
        return result
