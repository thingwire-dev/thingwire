"""MCP server: wires MQTT bridge, TD loader, tool compiler and the gateway pipeline.

Entry point that exposes compiled WoT TD tools to AI agents via the Model Context
Protocol. No business logic here: every device tool call goes through
`gateway_actions.ActionPipeline` (identity, taint, approval, safety, audit, record).
"""

import inspect
import logging
import os
from typing import Any

from fastmcp import FastMCP

from thingwire.audit_log import AuditLog
from thingwire.gateway_actions import ActionPipeline, CommandRoute
from thingwire.gateway_taint import TaintTracker
from thingwire.identity import (
    RESERVED_ACTION,
    IdentitySettings,
    check_sse_host,
    current_identity,
)
from thingwire.identity_gate import Gatekeeper, create_identity_server
from thingwire.mqtt_bridge import MqttBridge
from thingwire.safety import SafetyLayer
from thingwire.td_loader import ThingDescription
from thingwire.tool_compiler import CompiledTool, ToolParameter, compile_tools

# Map TD types to Python annotation types
_TYPE_MAP: dict[str, type] = {
    "boolean": bool,
    "number": float,
    "integer": int,
    "string": str,
}
_PIPELINE_ATTR = "thingwire_pipeline"

logger = logging.getLogger(__name__)


def _gate(mcp: FastMCP) -> Gatekeeper | None:
    """Gatekeeper of an identity-enabled server, None for stdio dev mode (identity off)."""
    return getattr(mcp, "gate", None)


def attach_pipeline(mcp: FastMCP, pipeline: ActionPipeline) -> ActionPipeline:
    """Use one pipeline (and one taint ledger) for every tool on this server."""
    setattr(mcp, _PIPELINE_ATTR, pipeline)
    return pipeline


def _pipeline_for(
    mcp: FastMCP, bridge: MqttBridge, safety: SafetyLayer, audit: AuditLog
) -> ActionPipeline:
    existing: ActionPipeline | None = getattr(mcp, _PIPELINE_ATTR, None)
    if existing is not None:
        return existing
    taint = TaintTracker.from_config(safety.config)
    return attach_pipeline(mcp, ActionPipeline(bridge, safety, audit, taint=taint))


def command_route(td: ThingDescription, action_name: str) -> CommandRoute:
    """Command fields the TD declares for an action; default is the action name itself."""
    action = td.actions[action_name]
    forms = [f for f in action.forms if f.op == "invokeaction"] or action.forms
    form = forms[0] if forms else None
    target = form.target if form and form.target else action_name
    command = form.command if form and form.command else action_name
    return CommandRoute(target=target, command=command)


def _register_read_tool(mcp: FastMCP, tool: CompiledTool, pipeline: ActionPipeline) -> None:
    """Register a read tool (sensor reading) on the MCP server."""

    async def read_handler() -> dict[str, Any]:
        return await pipeline.read(tool, current_identity(), _gate(mcp))

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


# Optional freshness stamp every action tool accepts (contracts 5.3). A TD input with the same
# name keeps its meaning and is not treated as a stamp.
_STAMP_PARAMS = (
    ToolParameter(
        name="issued_at",
        type="number",
        description="Optional: when this command was issued, seconds since the Unix epoch (UTC)",
        required=False,
    ),
    ToolParameter(
        name="nonce",
        type="string",
        description="Optional: unique 8-128 character id (A-Z a-z 0-9 _ -), never reused",
        required=False,
    ),
)


def _register_action_tool(
    mcp: FastMCP, tool: CompiledTool, route: CommandRoute, pipeline: ActionPipeline
) -> None:
    """Register an action tool (actuator command) on the MCP server."""
    taken = {p.name for p in tool.parameters}
    stamps = [p for p in _STAMP_PARAMS if p.name not in taken]
    all_params = [*tool.parameters, *stamps]

    async def _execute(params: dict[str, Any]) -> dict[str, Any]:
        stamp = {p.name: params.pop(p.name, None) for p in stamps}
        return await pipeline.execute(
            tool,
            route,
            params,
            current_identity(),
            _gate(mcp),
            issued_at=stamp.get("issued_at"),
            nonce=stamp.get("nonce"),
        )

    # Build a wrapper with explicit parameter signature for FastMCP.
    # FastMCP inspects __signature__ AND __annotations__ via get_type_hints().
    sig = _build_action_signature(all_params)
    annotations: dict[str, Any] = {p.name: _TYPE_MAP.get(p.type, str) for p in all_params}
    annotations["return"] = dict[str, Any]

    async def action_handler(*args: Any, **kwargs: Any) -> dict[str, Any]:
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        return await _execute(dict(bound.arguments))

    action_handler.__signature__ = sig  # type: ignore[attr-defined]
    action_handler.__annotations__ = annotations
    mcp.tool(name=tool.name, description=tool.description)(action_handler)


def register_device_tools(
    mcp: FastMCP,
    td: ThingDescription,
    bridge: MqttBridge,
    safety: SafetyLayer,
    audit: AuditLog,
    device_prefix: str | None = None,
    *,
    device_id: str | None = None,
) -> list[str]:
    """Compile TD into tools and register them on the MCP server.

    `device_id` is the MQTT id the bridge and safety layer know the device by; it
    defaults to the TD id. Returns list of registered tool names.
    """
    pipeline = _pipeline_for(mcp, bridge, safety, audit)
    tools = compile_tools(td, device_prefix=device_prefix)
    if device_id is not None:
        tools = [t.model_copy(update={"device_id": device_id}) for t in tools]
    registered: list[str] = []
    for tool in tools:
        if tool.tool_type == "action" and tool.source_name == RESERVED_ACTION:
            logger.error("Refusing tool for action named '%s' (reserved scope)", RESERVED_ACTION)
            continue
        if tool.tool_type == "read":
            _register_read_tool(mcp, tool, pipeline)
        elif tool.tool_type == "action":
            _register_action_tool(mcp, tool, command_route(td, tool.source_name), pipeline)
        registered.append(tool.name)
        logger.info("Registered MCP tool: %s", tool.name)

    return registered


def register_meta_tools(
    mcp: FastMCP,
    bridge: MqttBridge,
    audit: AuditLog,
    safety: SafetyLayer | None = None,
) -> None:
    """Register meta-tools for device management, audit and (with safety) the e-stop trip."""

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

    if safety is None:
        return
    pipeline = _pipeline_for(mcp, bridge, safety, audit)

    @mcp.tool()
    async def emergency_stop(device_id: str, reason: str = "") -> dict[str, Any]:
        """Latch the emergency stop for a device: it refuses motion until a human resets it.

        Agents can stop a device but can never reset the stop.
        """
        known = device_id in bridge.get_devices()
        return await pipeline.emergency_stop(
            device_id, reason, known, current_identity(), _gate(mcp)
        )


def create_mcp_server(name: str = "ThingWire") -> FastMCP:
    """Create a FastMCP server: authenticated HTTP when THINGWIRE_TRANSPORT=http, else plain."""
    if os.environ.get("THINGWIRE_TRANSPORT") == "sse":
        check_sse_host(os.environ.get("THINGWIRE_SSE_HOST"))  # raises IdentityError: fail closed
    if os.environ.get("THINGWIRE_TRANSPORT") == "http":
        return create_identity_server(name, IdentitySettings())  # raises IdentityError: fail closed
    return FastMCP(name)
