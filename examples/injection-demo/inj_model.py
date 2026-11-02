"""Data model for the injection benchmark: events, cases, harm rules and results."""

from dataclasses import dataclass, field
from typing import Any, Literal

EventKind = Literal["say", "mqtt", "read_tools", "advance", "silence", "estop", "set_reading"]
HarmKind = Literal["any", "invalid", "over", "slew"]
HumanMode = Literal["absent", "approves_once"]

OPERATOR = "operator"
CHANNELS = (
    "operator",
    "tool_result",
    "scene_text",
    "mqtt_payload",
    "sensor_reading",
    "td_description",
)
CATEGORIES = (
    "direct_command_injection",
    "scene_text_injection",
    "sensor_value_injection",
    "td_description_poisoning",
    "confused_deputy",
    "motion_limit_violation",
    "replayed_or_stale_command",
    "estop_evasion",
    "rate_limit_flooding",
    "tainted_data_to_actuator",
)


@dataclass(frozen=True)
class Event:
    """One step of a case timeline. Only the fields of its kind are used."""

    kind: EventKind
    channel: str = ""  # say: who or what the text came from
    text: str = ""  # say: text the agent reads
    topic: str = ""  # mqtt
    payload: dict[str, Any] = field(default_factory=dict)  # mqtt
    seconds: float = 0.0  # advance
    device: str = ""  # read_tools, estop (empty = global), set_reading
    prop: str = ""  # set_reading
    value: Any = None  # set_reading
    stale_by: float = 0.0  # say, mqtt: calls made from this text carry a stamp this old


@dataclass(frozen=True)
class Harm:
    """What counts as the attack succeeding, judged on commands that reached the device."""

    kind: HarmKind
    devices: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    limit: int | None = None  # over: None means the rate limit of the safety config


@dataclass(frozen=True)
class Case:
    """One labelled attack. `expected_safe` is the outcome a safe system must produce."""

    id: str
    category: str
    channel: str
    title: str
    events: tuple[Event, ...]
    harm: Harm
    expected_safe: str
    expected_codes: tuple[str, ...]
    scopes: tuple[str, ...] = ()  # granted on top of the default agent scopes
    human: HumanMode = "absent"
    td_patch: str = ""
    gap_hint: str = ""  # shown only when the gateway fails to block the case


@dataclass(frozen=True)
class Benign:
    """A legitimate task. It passes when every operator call succeeds."""

    id: str
    title: str
    events: tuple[Event, ...]
    expected_calls: int
    scopes: tuple[str, ...] = ()
    human: HumanMode = "absent"


@dataclass(frozen=True)
class Step:
    """One tool call the agent made and what the arm did with it."""

    channel: str
    tool: str
    ok: bool  # delivered (actions), answered (reads), latched (e-stop)
    code: str
    delivered: bool


@dataclass(frozen=True)
class Delivery:
    """A command that reached the device adapter."""

    device: str
    command: str
    value: dict[str, Any]
    origin: str
    executed: bool
    at: float
