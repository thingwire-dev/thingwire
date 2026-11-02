"""Input rules for the desk bot `show_text` and `blink` actions (contracts 5.6).

Fail closed and never rewrite: a violation is a deny, nothing is truncated or clamped. The
charset is fixed here regardless of what a TD claims; the length and numeric bounds come from
the merged limits (the stricter of the TD and the operator pin). Denial details never carry
the rejected text.
"""

from typing import Any

from thingwire.safety_motion import AxisLimit, Decision

ASCII_FIRST = 0x20
ASCII_LAST = 0x7E

# action -> (axis -> kind, required axes)
TEXT_ACTIONS: dict[str, tuple[dict[str, str], tuple[str, ...]]] = {
    "do_show_text": ({"text": "text", "duration_ms": "int"}, ("text",)),
    "do_blink": ({"times": "int"}, ("times",)),
}


def _invalid(axis: str, reason: str, message: str, **extra: Any) -> Decision:
    return Decision(False, "INVALID_INPUT", message, {"axis": axis, "reason": reason, **extra})


def _text_problem(value: Any, lim: AxisLimit) -> Decision | None:
    if not isinstance(value, str):
        return _invalid("text", "not_string", "'text' must be a string")
    if any(ord(c) < ASCII_FIRST or ord(c) == 0x7F for c in value):
        return _invalid("text", "control_char", "'text' contains a control character")
    if any(ord(c) > ASCII_LAST for c in value):
        return _invalid("text", "charset", "'text' must be printable ASCII")
    if not value.strip(" "):
        return _invalid("text", "blank", "'text' is blank")
    if lim.max_length is None:
        return _invalid("text", "no_limit", "no text length limit is known for this device")
    if len(value) > lim.max_length:
        return _invalid(
            "text",
            "too_long",
            f"'text' is longer than {lim.max_length} characters",
            max_length=lim.max_length,
        )
    return None


def _int_problem(axis: str, value: Any, lim: AxisLimit) -> Decision | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return _invalid(axis, "not_integer", f"'{axis}' must be an integer")
    below = lim.minimum is not None and value < lim.minimum
    above = lim.maximum is not None and value > lim.maximum
    if below or above:
        return Decision(
            False,
            "OUT_OF_RANGE",
            f"'{axis}'={value} is outside [{lim.minimum}, {lim.maximum}]",
            {"axis": axis, "value": value, "minimum": lim.minimum, "maximum": lim.maximum},
        )
    return None


def check_text_call(
    action: str, params: dict[str, Any], limits: dict[str, AxisLimit]
) -> Decision | None:
    """Deny decision for a bad show_text or blink call, or None when it is fine or not ours."""
    rule = TEXT_ACTIONS.get(action)
    if rule is None:
        return None
    kinds, required = rule
    for key in params:
        if key not in limits and key not in required:
            return _invalid(key, "unknown_property", f"'{key}' is not an input of this action")
    for axis in required:
        if axis not in params:
            return _invalid(axis, "missing", f"'{axis}' is required")
    for axis, kind in kinds.items():
        if axis not in params:
            continue
        lim = limits.get(axis, AxisLimit())
        problem = (
            _text_problem(params[axis], lim)
            if kind == "text"
            else _int_problem(axis, params[axis], lim)
        )
        if problem is not None:
            return problem
    return None
