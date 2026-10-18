"""MCP server — wires MQTT bridge, TD loader, tool compiler, and safety layer.

This is the entry point that exposes compiled WoT TD tools to AI agents
via the Model Context Protocol. No business logic here — just wiring.
"""

import inspect
import logging
import os
from typing import Any

from fastmcp import FastMCP

from thingwire.audit_log import AuditLog
from thingwire.identity import (
    RESERVED_ACTION,
    AgentIdentity,
    IdentitySettings,
    check_sse_host,
    current_identity,
)
from thingwire.identity_gate import (
    Gatekeeper,
    Verdict,
    create_identity_server,
    recheck_safety,
    record_audit,
)
from thingwire.mqtt_bridge import MqttBridge
from thingwire.safety import SafetyError, SafetyLayer
from thingwire.td_loader import ThingDescription
from thingwire.tool_compiler import CompiledTool, ToolParameter, compile_tools

# Map TD types to Python annotation types
_TYPE_MAP: dict[str, type] = {
    "boolean": bool,
    "number": float,
    "integer": int,
    "string": str,
}

logger = logging.getLogger(__name__)
_ALLOW = Verdict(True, "allow")


def _gate(mcp: FastMCP) -> Gatekeeper | None:
    """Gatekeeper of an identity-enabled server, None for stdio dev mode (identity off)."""
    return getattr(mcp, "gate", None)


async def _deny(audit: AuditLog, tool: CompiledTool, params: dict[str, Any],
                ident: AgentIdentity | None, verdict: Verdict) -> dict[str, Any]:
    """Audit and return a gate denial."""
    await record_audit(audit, ident=ident, verdict=verdict, device_id=tool.device_id,
                       action=tool.name, params=params, result=verdict.error())
    return verdict.error()


def _register_read_tool(
    mcp: FastMCP,
    tool: CompiledTool,
    bridge: MqttBridge,
    audit: AuditLog,
) -> None:
    """Register a read tool (sensor reading) on the MCP server."""

    async def read_handler() -> dict[str, Any]:
        gate, ident = _gate(mcp), current_identity()
        if gate:
            verdict = gate.check_read(ident, tool.device_id)
            if not verdict.allowed:
                await record_audit(audit, ident=ident, verdict=verdict, device_id=tool.device_id,
                                   action=tool.name, params={}, result=verdict.error())
                return verdict.error()
        if not bridge.is_device_online(tool.device_id):
            return {
                "error": "DEVICE_OFFLINE",
                "message": f"Device '{tool.device_id}' is offline. Cannot read {tool.source_name}.",
                "device_id": tool.device_id,
            }
        result = bridge.get_latest_reading(tool.device_id, tool.source_name)
        await record_audit(audit, ident=ident, verdict=_ALLOW if gate else None,
                           device_id=tool.device_id, action=tool.name, params={}, result=result)
        return result

    mcp.tool(name=tool.name, description=tool.description)(read_handler)


def _build_action_signature(params: list[ToolParameter]) -> inspect.Signature:
    """Build a proper function signature from tool parameters."""
    sig_params = []
    for p in params:
        annotation = _TYPE_MAP.get(p.type, str)
        if p.required:
            sig_params.append(
                inspect.Parameter(
                    p.name,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    annotation=annotation,
                )
            )
        else:
            sig_params.append(
                inspect.Parameter(
                    p.name,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    default=None,
                    annotation=annotation | None,
                )
            )
    return inspect.Signature(sig_params, return_annotation=dict[str, Any])


def _register_action_tool(
    mcp: FastMCP,
    tool: CompiledTool,
    bridge: MqttBridge,
    safety: SafetyLayer,
    audit: AuditLog,
) -> None:
    """Register an action tool (actuator command) on the MCP server."""

    async def _execute(params: dict[str, Any]) -> dict[str, Any]:
        gate, ident = _gate(mcp), current_identity()
        dangerous = not tool.safe or safety.is_dangerous(tool.device_id, tool.name)
        verdict = (
            gate.check_scope(ident, tool.device_id, tool.source_name, dangerous) if gate else None
        )
        if verdict and not verdict.allowed:
            return await _deny(audit, tool, params, ident, verdict)
        try:
            # Sync heartbeat from telemetry before deadman check
            safety.update_heartbeat_from_telemetry(
                tool.device_id,
                bridge.get_last_telemetry_time(tool.device_id),
            )
            safety.guards.check_estop(tool.device_id, tool.name)
            safety.check_permission(tool.device_id, tool.name)
            safety.check_rate_limit(tool.device_id, tool.name)
            safety.check_deadman_switch(tool.device_id)
        except SafetyError as e:
            error_result = {"error": e.code, "message": e.message}
            await record_audit(audit, ident=ident, verdict=verdict, device_id=tool.device_id,
                               action=tool.name, params=params, result=error_result)
            return error_result

        confirmed = False
        if safety.is_dangerous(tool.device_id, tool.name):
            safety.require_confirmation(tool.name, params)
            confirmed = True
        if gate and ident and dangerous:
            verdict = await gate.wait_approval(ident, tool.device_id, tool.source_name, params)
            if verdict.allowed:
                verdict = recheck_safety(safety, bridge, tool.device_id, tool.name) or verdict
            if not verdict.allowed:
                return await _deny(audit, tool, params, ident, verdict)

        target = tool.source_name.replace("set", "").lower() + "1"
        value = params.get("state", params)

        try:
            action_id = await bridge.send_command(
                device_id=tool.device_id,
                target=target,
                command="set",
                value=value,
            )
        except RuntimeError as e:
            error_result = {
                "error": "COMMAND_FAILED",
                "message": str(e),
                "device_id": tool.device_id,
            }
            await record_audit(audit, ident=ident, verdict=verdict, device_id=tool.device_id,
                               action=tool.name, params=params, result=error_result)
            return error_result

        result = {
            "status": "command_sent",
            "action_id": action_id,
            "device_id": tool.device_id,
            "target": target,
            "value": value,
        }

        await record_audit(audit, ident=ident, verdict=verdict, device_id=tool.device_id,
                           action=tool.name, params=params, result=result, confirmed=confirmed)
        return result

    # Build a wrapper with explicit parameter signature for FastMCP.
    # FastMCP inspects __signature__ AND __annotations__ via get_type_hints().
    if tool.parameters:
        sig = _build_action_signature(tool.parameters)
        annotations: dict[str, Any] = {p.name: _TYPE_MAP.get(p.type, str) for p in tool.parameters}
        annotations["return"] = dict[str, Any]

        async def action_handler(*args: Any, **kwargs: Any) -> dict[str, Any]:
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            return await _execute(dict(bound.arguments))

        action_handler.__signature__ = sig  # type: ignore[attr-defined]
        action_handler.__annotations__ = annotations
    else:

        async def action_handler() -> dict[str, Any]:  # type: ignore[misc]
            return await _execute({})

    mcp.tool(name=tool.name, description=tool.description)(action_handler)


def register_device_tools(
    mcp: FastMCP,
    td: ThingDescription,
    bridge: MqttBridge,
    safety: SafetyLayer,
    audit: AuditLog,
    device_prefix: str | None = None,
) -> list[str]:
    """Compile TD into tools and register them on the MCP server.

    Returns list of registered tool names.
    """
    tools = compile_tools(td, device_prefix=device_prefix)
    registered: list[str] = []
    for tool in tools:
        if tool.tool_type == "action" and tool.source_name == RESERVED_ACTION:
            logger.error("Refusing tool for action named '%s' (reserved scope)", RESERVED_ACTION)
            continue
        if tool.tool_type == "read":
            _register_read_tool(mcp, tool, bridge, audit)
        elif tool.tool_type == "action":
            _register_action_tool(mcp, tool, bridge, safety, audit)
        registered.append(tool.name)
        logger.info("Registered MCP tool: %s", tool.name)

    return registered


def register_meta_tools(
    mcp: FastMCP,
    bridge: MqttBridge,
    audit: AuditLog,
) -> None:
    """Register meta-tools for device management and audit."""

    @mcp.tool()
    async def list_devices() -> dict[str, Any]:
        """List all discovered ThingWire devices and their status."""
        devices = bridge.get_devices()
        gate, ident = _gate(mcp), current_identity()
        result: list[dict[str, str]] = []
        for device_id in devices:
            if gate and not gate.check_read(ident, device_id).allowed:
                continue  # hide devices the agent cannot read
            td = bridge.get_td(device_id)
            result.append(
                {
                    "device_id": device_id,
                    "title": td.title if td else "unknown",
                    "status": bridge.get_device_status(device_id),
                }
            )
        return {"devices": result, "count": len(result)}

    @mcp.tool()
    async def get_device_status(device_id: str) -> dict[str, Any]:
        """Get detailed status for a specific device."""
        gate = _gate(mcp)
        if gate and not (v := gate.check_read(current_identity(), device_id)).allowed:
            return v.error()
        td = bridge.get_td(device_id)
        if not td:
            return {"error": f"Device '{device_id}' not found"}
        return {
            "device_id": device_id,
            "title": td.title,
            "description": td.description,
            "status": bridge.get_device_status(device_id),
            "properties": list(td.properties.keys()),
            "actions": list(td.actions.keys()),
        }

    @mcp.tool()
    async def get_audit_log(device_id: str | None = None, limit: int = 20) -> dict[str, Any]:
        """Get recent command audit log entries."""
        gate = _gate(mcp)
        if gate and not (v := gate.check_named_scope(current_identity(), "audit:read")).allowed:
            return v.error()
        entries = await audit.get_recent(device_id=device_id, limit=limit)
        return {"entries": entries, "count": len(entries)}


def create_mcp_server(name: str = "ThingWire") -> FastMCP:
    """Create a FastMCP server: authenticated HTTP when THINGWIRE_TRANSPORT=http, else plain."""
    if os.environ.get("THINGWIRE_TRANSPORT") == "sse":
        check_sse_host(os.environ.get("THINGWIRE_SSE_HOST"))  # raises IdentityError: fail closed
    if os.environ.get("THINGWIRE_TRANSPORT") == "http":
        return create_identity_server(name, IdentitySettings())  # raises IdentityError: fail closed
    return FastMCP(name)
