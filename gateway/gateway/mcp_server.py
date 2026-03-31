"""MCP server — wires MQTT bridge, TD loader, tool compiler, and safety layer.

This is the entry point that exposes compiled WoT TD tools to AI agents
via the Model Context Protocol. No business logic here — just wiring.
"""

import inspect
import logging
from typing import Any

from fastmcp import FastMCP

from gateway.audit_log import AuditLog
from gateway.config import GatewayConfig
from gateway.mqtt_bridge import MqttBridge
from gateway.safety import SafetyError, SafetyLayer
from gateway.td_loader import ThingDescription
from gateway.tool_compiler import CompiledTool, ToolParameter, compile_tools

# Map TD types to Python annotation types
_TYPE_MAP: dict[str, type] = {
    "boolean": bool,
    "number": float,
    "integer": int,
    "string": str,
}

logger = logging.getLogger(__name__)


def _register_read_tool(
    mcp: FastMCP,
    tool: CompiledTool,
    bridge: MqttBridge,
    audit: AuditLog,
) -> None:
    """Register a read tool (sensor reading) on the MCP server."""

    async def read_handler() -> dict[str, Any]:
        result = bridge.get_latest_reading(tool.device_id, tool.source_name)
        await audit.record(
            device_id=tool.device_id,
            action=tool.name,
            params={},
            result=result,
        )
        return result

    # FastMCP uses the function name and docstring for tool metadata
    read_handler.__name__ = tool.name
    read_handler.__doc__ = tool.description

    mcp.tool()(read_handler)


def _build_action_signature(params: list[ToolParameter]) -> inspect.Signature:
    """Build a proper function signature from tool parameters."""
    sig_params = []
    for p in params:
        annotation = _TYPE_MAP.get(p.type, str)
        if p.required:
            sig_params.append(
                inspect.Parameter(p.name, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=annotation)
            )
        else:
            sig_params.append(
                inspect.Parameter(
                    p.name, inspect.Parameter.POSITIONAL_OR_KEYWORD, default=None, annotation=annotation | None  # type: ignore[operator]
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

    # Build the inner handler that captures tool/bridge/safety/audit
    async def _execute(params: dict[str, Any]) -> dict[str, Any]:
        try:
            safety.check_permission(tool.device_id, tool.name)
            safety.check_rate_limit(tool.device_id, tool.name)
            safety.check_deadman_switch(tool.device_id)
        except SafetyError as e:
            error_result = {"error": e.code, "message": e.message}
            await audit.record(
                device_id=tool.device_id,
                action=tool.name,
                params=params,
                result=error_result,
            )
            return error_result

        confirmed = False
        if safety.is_dangerous(tool.device_id, tool.name):
            safety.require_confirmation(tool.name, params)
            confirmed = True

        # Extract target from source_name (e.g., "setRelay" -> "relay1")
        target = tool.source_name.replace("set", "").lower() + "1"
        value = params.get("state", params)

        action_id = await bridge.send_command(
            device_id=tool.device_id,
            target=target,
            command="set",
            value=value,
        )

        result = {
            "status": "command_sent",
            "action_id": action_id,
            "device_id": tool.device_id,
            "target": target,
            "value": value,
        }

        await audit.record(
            device_id=tool.device_id,
            action=tool.name,
            params=params,
            result=result,
            confirmed=confirmed,
        )
        return result

    # Build a wrapper with explicit parameter signature for FastMCP.
    # FastMCP inspects __signature__ AND __annotations__ via get_type_hints().
    if tool.parameters:
        sig = _build_action_signature(tool.parameters)
        annotations: dict[str, Any] = {
            p.name: _TYPE_MAP.get(p.type, str) for p in tool.parameters
        }
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

    action_handler.__name__ = tool.name
    action_handler.__doc__ = tool.description

    mcp.tool()(action_handler)


def register_device_tools(
    mcp: FastMCP,
    td: ThingDescription,
    bridge: MqttBridge,
    safety: SafetyLayer,
    audit: AuditLog,
) -> list[str]:
    """Compile TD into tools and register them on the MCP server.

    Returns list of registered tool names.
    """
    tools = compile_tools(td)
    registered: list[str] = []

    for tool in tools:
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
        result: list[dict[str, str]] = []
        for device_id in devices:
            td = bridge.get_td(device_id)
            result.append({
                "device_id": device_id,
                "title": td.title if td else "unknown",
                "status": bridge.get_device_status(device_id),
            })
        return {"devices": result, "count": len(result)}

    @mcp.tool()
    async def get_device_status(device_id: str) -> dict[str, Any]:
        """Get detailed status for a specific device."""
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
        entries = await audit.get_recent(device_id=device_id, limit=limit)
        return {"entries": entries, "count": len(entries)}


def create_mcp_server(name: str = "ThingWire") -> FastMCP:
    """Create a new FastMCP server instance."""
    return FastMCP(name)
