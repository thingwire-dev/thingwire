"""Personality: the one place timings, probabilities and expressions live.

Loading fails closed: a missing file, a missing or unknown key, a wrong type, an
out-of-range number or an expression outside the TD enum raises PersonalityError.
"""

import math
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

UTTERANCE_RE = re.compile(r"^[A-Za-z0-9 ,.!?'-]+$")


class PersonalityError(ValueError):
    """The personality file is unusable. The loop must not start."""


def valid_utterance(text: object, max_chars: int) -> bool:
    """A short plain string: letters, digits and light punctuation only."""
    return (
        isinstance(text, str)
        and 0 < len(text) <= max_chars
        and UTTERANCE_RE.fullmatch(text) is not None
    )


@dataclass(frozen=True)
class Personality:
    tick_s: float
    min_action_gap_s: float
    trigger_after_s: float
    idle_expr: str
    notice_expr: str
    react_exprs: tuple[str, ...]
    cooldown_expr: str
    sleep_expr: str
    blink_every: tuple[float, float]
    look_every: tuple[float, float]
    look_pan: float
    look_tilt: float
    curiosity: float
    curious_expr: str
    notice_s: float
    front_pan: float
    front_tilt: float
    react_s: float
    utterance_p: float
    nod_p: float
    nod_times: int
    cooldown_s: float
    sleep_after_s: float
    sleep_tilt: float
    backoff_initial_s: float
    backoff_max_s: float
    max_utterance_chars: int
    utterances: tuple[str, ...]


def _section(data: Mapping[str, Any], key: str, keys: Collection[str]) -> Mapping[str, Any]:
    sec = data.get(key)
    if not isinstance(sec, Mapping):
        raise PersonalityError(f"missing section '{key}'")
    _exact(sec, keys, key)
    return sec


def _exact(sec: Mapping[str, Any], keys: Collection[str], where: str) -> None:
    missing, extra = set(keys) - set(sec), set(sec) - set(keys)
    if missing or extra:
        raise PersonalityError(f"{where}: missing {sorted(missing)} unknown {sorted(extra)}")


def _num(sec: Mapping[str, Any], key: str, lo: float, hi: float = math.inf) -> float:
    v = sec[key]
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
        raise PersonalityError(f"'{key}' must be a finite number")
    if not lo <= v <= hi:
        raise PersonalityError(f"'{key}' must be in [{lo}, {hi}]")
    return float(v)


def _pair(sec: Mapping[str, Any], key: str) -> tuple[float, float]:
    v = sec[key]
    if not isinstance(v, list) or len(v) != 2:
        raise PersonalityError(f"'{key}' must be [min, max]")
    lo = _num({"x": v[0]}, "x", 0.1)
    hi = _num({"x": v[1]}, "x", lo)
    return lo, hi


def _expr(name: object, allowed: Collection[str], where: str) -> str:
    if not isinstance(name, str) or name not in allowed:
        raise PersonalityError(f"{where}: '{name}' is not a TD expression")
    return name


def parse_personality(data: object, allowed_exprs: Collection[str]) -> Personality:
    """Validate raw YAML data against the TD expression enum."""
    if not isinstance(data, Mapping):
        raise PersonalityError("personality must be a mapping")
    top = ("tick_s", "min_action_gap_s", "trigger_after_s")
    sections = ("expressions", "idle", "notice", "react", "cooldown", "sleep", "backoff", "menu")
    _exact(data, (*top, *sections), "top level")
    ex = _section(data, "expressions", ("idle", "notice", "react", "cooldown", "sleep"))
    idle = _section(
        data,
        "idle",
        (
            "blink_every_s",
            "look_every_s",
            "look_pan_deg",
            "look_tilt_deg",
            "curiosity",
            "curious_expression",
        ),
    )
    notice = _section(data, "notice", ("duration_s", "front_pan_deg", "front_tilt_deg"))
    react = _section(
        data, "react", ("duration_s", "utterance_probability", "nod_probability", "nod_times")
    )
    cool = _section(data, "cooldown", ("duration_s",))
    sleep = _section(data, "sleep", ("after_idle_s", "tilt_deg"))
    back = _section(data, "backoff", ("initial_s", "max_s"))
    menu = _section(data, "menu", ("max_utterance_chars", "utterances"))
    react_list = ex["react"]
    if not isinstance(react_list, list) or not react_list:
        raise PersonalityError("expressions.react must be a non-empty list")
    max_chars = int(_num(menu, "max_utterance_chars", 1, 80))
    words = menu["utterances"]
    if not isinstance(words, list) or not all(valid_utterance(w, max_chars) for w in words):
        raise PersonalityError("menu.utterances must be short plain strings")
    nod_times = _num(react, "nod_times", 1)
    if nod_times != int(nod_times):
        raise PersonalityError("'nod_times' must be a whole number")
    return Personality(
        tick_s=_num(data, "tick_s", 0.01),
        min_action_gap_s=_num(data, "min_action_gap_s", 0.0),
        trigger_after_s=_num(data, "trigger_after_s", 0.0),
        idle_expr=_expr(ex["idle"], allowed_exprs, "expressions.idle"),
        notice_expr=_expr(ex["notice"], allowed_exprs, "expressions.notice"),
        react_exprs=tuple(_expr(e, allowed_exprs, "expressions.react") for e in react_list),
        cooldown_expr=_expr(ex["cooldown"], allowed_exprs, "expressions.cooldown"),
        sleep_expr=_expr(ex["sleep"], allowed_exprs, "expressions.sleep"),
        blink_every=_pair(idle, "blink_every_s"),
        look_every=_pair(idle, "look_every_s"),
        look_pan=_num(idle, "look_pan_deg", 0.0),
        look_tilt=_num(idle, "look_tilt_deg", 0.0),
        curiosity=_num(idle, "curiosity", 0.0, 1.0),
        curious_expr=_expr(idle["curious_expression"], allowed_exprs, "idle.curious_expression"),
        notice_s=_num(notice, "duration_s", 0.0),
        front_pan=_num(notice, "front_pan_deg", -math.inf),
        front_tilt=_num(notice, "front_tilt_deg", -math.inf),
        react_s=_num(react, "duration_s", 0.0),
        utterance_p=_num(react, "utterance_probability", 0.0, 1.0),
        nod_p=_num(react, "nod_probability", 0.0, 1.0),
        nod_times=int(nod_times),
        cooldown_s=_num(cool, "duration_s", 0.0),
        sleep_after_s=_num(sleep, "after_idle_s", 0.0),
        sleep_tilt=_num(sleep, "tilt_deg", -math.inf),
        backoff_initial_s=_num(back, "initial_s", 0.0),
        backoff_max_s=_num(back, "max_s", _num(back, "initial_s", 0.0)),
        max_utterance_chars=max_chars,
        utterances=tuple(words),
    )


def load_personality(path: Path, allowed_exprs: Collection[str]) -> Personality:
    """Read and validate personality.yaml. Any problem raises PersonalityError."""
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise PersonalityError(f"cannot read {path}: {exc}") from exc
    return parse_personality(data, allowed_exprs)
