"""Small constructors so corpus entries stay one line per event."""

from typing import Any

from inj_model import Event


def say(channel: str, text: str) -> Event:
    return Event("say", channel=channel, text=text)


def mqtt(topic: str, payload: dict[str, Any], stale_by: float = 0.0) -> Event:
    return Event("mqtt", topic=topic, payload=payload, stale_by=stale_by)


def read_tools(device: str) -> Event:
    return Event("read_tools", device=device)


def advance(seconds: float) -> Event:
    return Event("advance", seconds=seconds)


def silence() -> Event:
    return Event("silence")


def estop(device: str = "") -> Event:
    return Event("estop", device=device)


def set_reading(device: str, prop: str, value: Any) -> Event:
    return Event("set_reading", device=device, prop=prop, value=value)
