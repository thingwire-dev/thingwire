"""WoT Thing Description loader and parser.

Pure parsing module — no I/O. Converts raw JSON into typed Pydantic models
for use by the tool compiler and other gateway components.
"""

import json
import logging

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class PropertyForm(BaseModel):
    """MQTT form link for a property."""

    href: str
    op: str


class ThingProperty(BaseModel):
    """A WoT TD property (sensor reading)."""

    type: str
    unit: str | None = None
    read_only: bool = Field(default=False, alias="readOnly")
    description: str
    forms: list[PropertyForm]

    model_config = ConfigDict(populate_by_name=True)


class ActionInputProperty(BaseModel):
    """A single property within an action's input schema."""

    type: str
    description: str | None = None


class ActionInput(BaseModel):
    """Input schema for a WoT TD action."""

    type: str
    properties: dict[str, ActionInputProperty]
    required: list[str] = Field(default_factory=list)


class ActionForm(BaseModel):
    """MQTT form link for an action."""

    href: str
    op: str


class ThingAction(BaseModel):
    """A WoT TD action (actuator command)."""

    title: str
    description: str
    input: ActionInput | None = None
    safe: bool = True
    idempotent: bool = False
    forms: list[ActionForm]


class SecurityDefinition(BaseModel):
    """Security scheme definition."""

    scheme: str


class ThingDescription(BaseModel):
    """W3C WoT Thing Description — the canonical device capability format."""

    context: str = Field(alias="@context")
    type: str = Field(alias="@type")
    id: str
    title: str
    description: str
    security_definitions: dict[str, SecurityDefinition] = Field(alias="securityDefinitions")
    security: list[str]
    properties: dict[str, ThingProperty] = Field(default_factory=dict)
    actions: dict[str, ThingAction] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)


def parse_thing_description(raw_json: str) -> ThingDescription:
    """Parse raw JSON string into a ThingDescription model.

    Raises ValueError with clear message if JSON is invalid or missing required fields.
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        msg = f"Invalid JSON in Thing Description: {e}"
        raise ValueError(msg) from e

    return parse_thing_description_dict(data)


def parse_thing_description_dict(data: dict) -> ThingDescription:  # type: ignore[type-arg]
    """Parse a dict into a ThingDescription model."""
    try:
        return ThingDescription.model_validate(data)
    except Exception as e:
        msg = f"Invalid Thing Description: {e}"
        logger.error(msg)
        raise ValueError(msg) from e
