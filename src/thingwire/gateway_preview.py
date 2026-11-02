"""Dry run of an action call: would it be allowed, and does it need a human (contracts 5.7).

Nothing is sent, approved, recorded in MCAP or counted. The one side effect is an audit row.
"""

from typing import Any

from thingwire.gateway_base import Call, PipelineBase
from thingwire.gateway_taint import LOCAL_SESSION
from thingwire.identity import AgentIdentity
from thingwire.identity_gate import Gatekeeper
from thingwire.safety_codes import DecisionCode
from thingwire.safety_preview import preview
from thingwire.taint_policy import CODE_REQUIRES_APPROVAL
from thingwire.tool_compiler import CompiledTool

CODE_MISSING_SCOPE = "MISSING_SCOPE"
CODE_IDENTITY_DENIED = "IDENTITY_DENIED"
CODE_DEVICE_OFFLINE = "DEVICE_OFFLINE"
AUDIT_DECISION = "dry_run"


def _result(
    allow: bool,
    code: str,
    message: str,
    *,
    needs_approval: bool = False,
    details: dict[str, Any] | None = None,
    value: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "dry_run",
        "would_allow": allow,
        "needs_approval": needs_approval,
        "code": str(code),
        "message": message,
        "details": details or {},
        "value": value,
        "not_evaluated": ["freshness"],
    }


async def dry_run(
    pl: PipelineBase,
    tool: CompiledTool,
    params: dict[str, Any],
    ident: AgentIdentity | None,
    gate: Gatekeeper | None,
) -> dict[str, Any]:
    """Judge a call with the real checks and change nothing."""
    call = Call(tool, {k: v for k, v in params.items() if v is not None}, ident)
    result = _judge(pl, call, gate)
    await pl._audit(call, result, AUDIT_DECISION, str(result["code"]))
    return result


def _judge(pl: PipelineBase, call: Call, gate: Gatekeeper | None) -> dict[str, Any]:
    dangerous = not call.tool.safe or pl.safety.is_dangerous(call.device, call.action)
    if gate is not None:
        v = gate.check_scope(call.ident, call.device, call.tool.source_name, dangerous)
        if not v.allowed:
            code = CODE_MISSING_SCOPE if v.required_scope else CODE_IDENTITY_DENIED
            return _result(False, code, v.reason)
    approval: tuple[str, str] | None = None
    if pl.taint is not None:
        session = call.ident.agent_id if call.ident else LOCAL_SESSION
        scopes = call.ident.scopes if call.ident else frozenset()
        taint = pl.taint.evaluate(
            session, call.tool.source_name, call.params, granted_scopes=scopes
        )
        if not taint.allowed and taint.code != CODE_REQUIRES_APPROVAL:
            return _result(False, taint.code, taint.message, details=taint.details)
        if not taint.allowed:
            approval = (taint.code, taint.message)
    verdict, safety_needs = preview(pl.safety, call.device, call.action, call.params)
    if not verdict.allowed:
        return _result(False, verdict.code, verdict.message, details=verdict.details)
    if not pl.bridge.is_device_online(call.device):
        return _result(False, CODE_DEVICE_OFFLINE, "device is offline")
    if approval is None and (dangerous or safety_needs):
        approval = (DecisionCode.APPROVAL_REQUIRED, "allowed once a human approves")
    if approval is not None:
        code, message = approval
        can_ask = gate is not None and call.ident is not None  # stdio mode cannot approve
        return _result(
            can_ask,
            code,
            message if can_ask else f"{message}; identity is not configured, so it is denied",
            needs_approval=True,
            details=verdict.details,
            value=verdict.params,
        )
    return _result(
        True, verdict.code, verdict.message, details=verdict.details, value=verdict.params
    )
