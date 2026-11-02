"""Non-mutating safety verdict for a dry run (contracts 5.7).

Runs the same checks as `SafetyLayer.evaluate`, in the same order, but commits nothing: no
latch or flood counter moves, no motion history or rate window advances. It reads the layer's
internals on purpose; keep it next to `safety.py` so a change to evaluate() is seen here.
"""

import logging
from typing import Any

from thingwire.safety import SafetyLayer
from thingwire.safety_codes import DecisionCode, SafetyError
from thingwire.safety_motion import Decision
from thingwire.safety_ratelimit import resolve_limits

logger = logging.getLogger(__name__)


def preview(
    layer: SafetyLayer, device_id: str, action: str, params: dict[str, Any]
) -> tuple[Decision, bool]:
    """(verdict, needs_approval). A dangerous action is judged as if it were approved."""
    try:
        layer.guards.check_estop(device_id, action)
        layer.guards.check_flood(device_id, action)
        layer.check_permission(device_id, action)
        layer.check_deadman_switch(device_id)
        verdict = layer._motion.check(
            device_id, action, params, layer._limits.get((device_id, action), {})
        )
        if not verdict.allowed:
            return verdict, False
        per_action, per_device = resolve_limits(layer.config, device_id, action)
        full = layer._rates.would_admit(device_id, action, per_action, per_device)
        if full is not None:
            scope, max_calls, window = full
            return (
                Decision(
                    False,
                    DecisionCode.RATE_LIMIT_EXCEEDED,
                    f"Rate limit exceeded for {scope} '{device_id}/{action}'. "
                    f"Max {max_calls:g} calls per {window:g}s",
                    {"scope": scope, "max_calls": max_calls, "window_seconds": window},
                ),
                False,
            )
    except SafetyError as exc:
        return Decision(False, exc.code, exc.message, exc.details), False
    except Exception:
        logger.exception("Safety preview failed for %s/%s", device_id, action)
        return Decision(False, DecisionCode.SAFETY_CHECK_ERROR, "safety preview failed"), False
    required = layer.config.get("global", {}).get("require_confirmation_for_dangerous", True)
    return verdict, bool(required and layer.is_dangerous(device_id, action))
