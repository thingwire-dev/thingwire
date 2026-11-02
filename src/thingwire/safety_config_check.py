"""Validate safety_config.yaml before the gateway starts (contracts 5.8).

A config that fails here stops the start: falling back to the built in defaults could be looser
than what the operator wrote, and a misspelt key would silently drop the protection it names.
Sections with their own validation (`taint`, `td_trust`, and the nested `estop`,
`denial_flood`, `command_freshness`) are left to their owners.
"""

from collections.abc import Mapping
from typing import Any

import click

TOP_KEYS = frozenset({"devices", "global", "taint", "td_trust"})
GLOBAL_KEYS = frozenset(
    {
        "require_confirmation_for_dangerous",
        "deadman_switch_timeout_seconds",
        "audit_log_retention_days",
        "default_rate_limit",
        "device_rate_limit",
        "out_of_range",
        "estop",
        "denial_flood",
        "command_freshness",
    }
)
DEVICE_KEYS = frozenset(
    {"allowed_actions", "dangerous_actions", "rate_limits", "device_rate_limit", "td_unpinned"}
)
OUT_OF_RANGE_MODES = ("reject", "clamp")


class ConfigError(click.UsageError):
    """The safety config is unusable; `thingwire serve` exits with status 2."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("invalid safety config:\n" + "\n".join(f"  - {p}" for p in problems))


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _unknown(where: str, section: Mapping[str, Any], known: frozenset[str]) -> list[str]:
    return [f"{where}: unknown key '{k}'" for k in sorted(map(str, section)) if k not in known]


def _rate(where: str, value: Any) -> list[str]:
    if not isinstance(value, Mapping):
        return [f"{where}: must be a mapping with max_calls and window_seconds"]
    calls: Any = value.get("max_calls")
    window: Any = value.get("window_seconds")
    problems = []
    if not isinstance(calls, int) or isinstance(calls, bool) or calls < 1:
        problems.append(f"{where}.max_calls: must be a positive integer")
    if not _number(window) or window <= 0:
        problems.append(f"{where}.window_seconds: must be a positive number")
    return problems


def _global(section: Mapping[str, Any]) -> list[str]:
    problems = _unknown("global", section, GLOBAL_KEYS)
    flag = section.get("require_confirmation_for_dangerous")
    if flag is not None and not isinstance(flag, bool):
        problems.append("global.require_confirmation_for_dangerous: must be true or false")
    deadman = section.get("deadman_switch_timeout_seconds")
    if deadman is not None and (not _number(deadman) or deadman <= 0):
        problems.append("global.deadman_switch_timeout_seconds: must be a positive number")
    keep = section.get("audit_log_retention_days")
    if keep is not None and (not _number(keep) or keep < 0):
        problems.append("global.audit_log_retention_days: must be a non negative number")
    mode = section.get("out_of_range")
    if mode is not None and str(mode).lower() not in OUT_OF_RANGE_MODES:
        problems.append("global.out_of_range: must be 'reject' or 'clamp'")
    for key in ("default_rate_limit", "device_rate_limit"):
        if section.get(key) is not None:
            problems += _rate(f"global.{key}", section[key])
    for key in ("estop", "denial_flood", "command_freshness"):
        if section.get(key) is not None and not isinstance(section[key], Mapping):
            problems.append(f"global.{key}: must be a mapping")
    return problems


def _device(name: str, section: Any) -> list[str]:
    where = f"devices.{name}"
    if not isinstance(section, Mapping):
        return [f"{where}: must be a mapping"]
    problems = _unknown(where, section, DEVICE_KEYS)
    for key in ("allowed_actions", "dangerous_actions"):
        value = section.get(key)
        if value is not None and not (
            isinstance(value, list) and all(isinstance(a, str) for a in value)
        ):
            problems.append(f"{where}.{key}: must be a list of action names")
    if section.get("device_rate_limit") is not None:
        problems += _rate(f"{where}.device_rate_limit", section["device_rate_limit"])
    limits = section.get("rate_limits")
    if limits is not None:
        if not isinstance(limits, Mapping):
            problems.append(f"{where}.rate_limits: must be a mapping of action to rate")
        else:
            for action, rate in limits.items():
                problems += _rate(f"{where}.rate_limits.{action}", rate)
    if section.get("td_unpinned") is not None and not isinstance(section["td_unpinned"], bool):
        problems.append(f"{where}.td_unpinned: must be true or false")
    return problems


def check_config(config: Any) -> None:
    """Raise ConfigError listing every problem; return quietly for a usable config."""
    if config is None:
        return  # an empty file means "no overrides"
    if not isinstance(config, Mapping):
        raise ConfigError(["top level: must be a mapping"])
    problems = _unknown("top level", config, TOP_KEYS)
    section = config.get("global")
    if section is not None:
        problems += _global(section) if isinstance(section, Mapping) else ["global: not a mapping"]
    devices = config.get("devices")
    if devices is not None:
        if isinstance(devices, Mapping):
            for name, dev in devices.items():
                problems += _device(str(name), dev)
        else:
            problems.append("devices: must be a mapping of device id to settings")
    if problems:
        raise ConfigError(problems)
