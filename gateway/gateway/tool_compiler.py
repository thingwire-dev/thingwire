"""Tool compiler — transforms WoT Thing Description into MCP tool definitions.

This is the core differentiator. Pure transformation, no I/O.
Takes a parsed ThingDescription and produces CompiledTool definitions
that the MCP server can register with AI agents.
"""

import logging
import re
from typing import Literal

from pydantic import BaseModel

from gateway.td_loader import ThingAction, ThingDescription, ThingProperty

logger = logging.getLogger(__name__)

DANGEROUS_PREFIX = "⚠️ PHYSICAL ACTION — "


class ToolParameter(BaseModel):
    """A single parameter for an MCP tool, derived from a TD action input schema."""

    name: str
    type: str  # "boolean", "number", "string"
    description: str | None
    required: bool


class CompiledTool(BaseModel):
    """An MCP tool definition compiled from a WoT TD property or action."""

    name: str  # e.g. "read_temperature", "do_set_relay"
    source_name: str  # Original TD property/action name (e.g. "temperature", "setRelay")
    device_id: str
    description: str
    parameters: list[ToolParameter]
    tool_type: Literal["read", "action"]
    safe: bool  # True for reads, from TD for actions
    idempotent: bool  # True for reads, from TD for actions


def _slugify(name: str) -> str:
    """Lowercase, replace spaces with underscores, strip non-alphanumeric except underscores.

    Also handles camelCase / PascalCase → snake_case conversion.
    """
    # Insert underscore before uppercase sequences (camelCase / PascalCase)
    s1 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    # Lowercase and replace spaces/hyphens with underscores
    s3 = s2.lower().replace("-", "_").replace(" ", "_")
    # Strip any remaining non-alphanumeric chars except underscores
    return re.sub(r"[^a-z0-9_]", "", s3)


def _returns_clause(prop_type: str, unit: str | None) -> str:
    """Build a 'Returns <type> in <unit>.' clause for property descriptions."""
    if unit:
        return f"Returns {prop_type} in {unit}."
    return f"Returns {prop_type}."


def _compile_property(
    device_id: str,
    prop_name: str,
    prop: ThingProperty,
) -> CompiledTool:
    """Compile a WoT TD property into a read tool."""
    slug = _slugify(prop_name)
    returns = _returns_clause(prop.type, prop.unit)
    description = f"Read {prop.description}. {returns}"

    return CompiledTool(
        name=f"read_{slug}",
        source_name=prop_name,
        device_id=device_id,
        description=description,
        parameters=[],
        tool_type="read",
        safe=True,
        idempotent=True,
    )


def _compile_action(
    device_id: str,
    action_name: str,
    action: ThingAction,
) -> CompiledTool:
    """Compile a WoT TD action into a do tool."""
    slug = _slugify(action_name)

    base_description = action.description
    if not action.safe:
        description = f"{DANGEROUS_PREFIX}{base_description}"
    else:
        description = base_description

    parameters: list[ToolParameter] = []
    if action.input:
        for param_name, param_def in action.input.properties.items():
            parameters.append(
                ToolParameter(
                    name=param_name,
                    type=param_def.type,
                    description=param_def.description,
                    required=param_name in action.input.required,
                )
            )

    return CompiledTool(
        name=f"do_{slug}",
        source_name=action_name,
        device_id=device_id,
        description=description,
        parameters=parameters,
        tool_type="action",
        safe=action.safe,
        idempotent=action.idempotent,
    )


def compile_tools(td: ThingDescription) -> list[CompiledTool]:
    """Compile a WoT Thing Description into MCP tool definitions.

    Each TD property becomes a read_* tool, each action becomes a do_* tool.
    Dangerous actions (safe=False) get a warning prefix in their description.
    """
    device_id = td.id
    tools: list[CompiledTool] = []

    for prop_name, prop in td.properties.items():
        tool = _compile_property(device_id, prop_name, prop)
        tools.append(tool)
        logger.debug("Compiled property '%s' → tool '%s'", prop_name, tool.name)

    for action_name, action in td.actions.items():
        tool = _compile_action(device_id, action_name, action)
        tools.append(tool)
        logger.debug("Compiled action '%s' → tool '%s'", action_name, tool.name)

    logger.info(
        "Compiled %d tools from TD '%s' (%d read, %d action)",
        len(tools),
        td.title,
        len(td.properties),
        len(td.actions),
    )

    return tools
