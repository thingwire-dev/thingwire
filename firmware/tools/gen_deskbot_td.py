"""Generate firmware/src/deskbot_gen.h and the desk bot TD from device-deskbot.yaml."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

FIRMWARE_DIR = Path(__file__).resolve().parent.parent
YAML_PATH = FIRMWARE_DIR / "device-deskbot.yaml"
HEADER_PATH = FIRMWARE_DIR / "src" / "deskbot_gen.h"
TD_URN = "urn:thingwire:device:{device_id}"
BASE = "mqtt://{broker}/thingwire/{device_id}"


def load_config(path: Path = YAML_PATH) -> dict[str, Any]:
    """Load the desk bot device config."""
    with path.open() as fh:
        return yaml.safe_load(fh)


def _axis_prop(name: str, axis: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "number",
        "unit": "degree",
        "minimum": axis["min"],
        "maximum": axis["max"],
        "maxSlewDegPerSec": axis["max_slew_deg_per_sec"],
        "readOnly": True,
        "description": f"Current head {name} angle",
        "forms": [{"href": f"{BASE}/telemetry", "op": "observeproperty"}],
    }


def _axis_input(axis: dict[str, Any]) -> dict[str, Any]:
    return {"type": "number", "unit": "degree", "minimum": axis["min"], "maximum": axis["max"]}


def _invoke_form() -> list[dict[str, str]]:
    return [{"href": f"{BASE}/command", "op": "invokeaction"}]


def _event_form() -> list[dict[str, str]]:
    return [{"href": f"{BASE}/events", "op": "subscribeevent"}]


def build_td(cfg: dict[str, Any]) -> dict[str, Any]:
    """Build the desk bot Thing Description (placeholders left unresolved)."""
    pan, tilt = cfg["servo"]["pan"], cfg["servo"]["tilt"]
    nod, sweep = cfg["nod"], cfg["sweep"]
    telemetry = [{"href": f"{BASE}/telemetry", "op": "observeproperty"}]
    return {
        "@context": "https://www.w3.org/2019/wot/td/v1.1",
        "@type": "Thing",
        "id": TD_URN,
        "title": cfg["title"],
        "description": cfg["description"],
        "securityDefinitions": {"nosec_sc": {"scheme": "nosec"}},
        "security": ["nosec_sc"],
        "properties": {
            "head_pan": _axis_prop("pan", pan),
            "head_tilt": _axis_prop("tilt", tilt),
            "expression": {
                "type": "string",
                "enum": cfg["expressions"],
                "readOnly": True,
                "description": "Expression shown on the OLED eyes",
                "forms": telemetry,
            },
            "person_detected": {
                "type": "boolean",
                "readOnly": True,
                "description": "True when the PIR sensor sees a person",
                "forms": telemetry,
            },
        },
        "actions": {
            "look_at": {
                "title": "Look at",
                "description": "Turn the head to a pan and tilt angle",
                "safe": True,
                "idempotent": True,
                "input": {
                    "type": "object",
                    "properties": {"pan": _axis_input(pan), "tilt": _axis_input(tilt)},
                    "required": ["pan", "tilt"],
                },
                "forms": _invoke_form(),
            },
            "set_expression": {
                "title": "Set expression",
                "description": "Change the OLED eye expression",
                "safe": True,
                "idempotent": True,
                "input": {
                    "type": "object",
                    "properties": {"expression": {"type": "string", "enum": cfg["expressions"]}},
                    "required": ["expression"],
                },
                "forms": _invoke_form(),
            },
            "nod": {
                "title": "Nod",
                "description": "Nod the head up and down",
                "safe": True,
                "idempotent": False,
                "input": {
                    "type": "object",
                    "properties": {
                        "times": {
                            "type": "integer",
                            "minimum": nod["min_times"],
                            "maximum": nod["max_times"],
                        }
                    },
                    "required": ["times"],
                },
                "forms": _invoke_form(),
            },
            "sweep": {
                "title": "Sweep",
                "description": "Sweep the head left and right. Needs approval.",
                "safe": False,
                "idempotent": False,
                "input": {
                    "type": "object",
                    "properties": {
                        "speed_deg_s": {
                            "type": "number",
                            "unit": "degree/second",
                            "minimum": sweep["min_speed_deg_s"],
                            "maximum": sweep["max_speed_deg_s"],
                        }
                    },
                    "required": ["speed_deg_s"],
                },
                "forms": _invoke_form(),
            },
        },
        "events": {
            "wake_word": {
                "description": "Wake word heard (ESP-SR, not yet implemented)",
                "data": {
                    "type": "object",
                    "properties": {
                        "phrase": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                },
                "forms": _event_form(),
            },
            "person_seen": {
                "description": "A person appeared in front of the bot",
                "data": {"type": "object", "properties": {"confidence": {"type": "number"}}},
                "forms": _event_form(),
            },
        },
    }


def render_header(cfg: dict[str, Any]) -> str:
    """Render deskbot_gen.h: config #defines plus the compact TD template."""
    p, s = cfg["pins"], cfg["servo"]
    pan, tilt = s["pan"], s["tilt"]
    exprs = cfg["expressions"]
    td = json.dumps(build_td(cfg), separators=(",", ":"))
    defs = [
        ("DESKBOT_DEVICE_ID", f'"{cfg["device_id"]}"'),
        ("DESKBOT_PIN_SERVO_PAN", p["servo_pan"]),
        ("DESKBOT_PIN_SERVO_TILT", p["servo_tilt"]),
        ("DESKBOT_PIN_PIR", p["pir"]),
        ("DESKBOT_PIN_I2C_SDA", p["i2c_sda"]),
        ("DESKBOT_PIN_I2C_SCL", p["i2c_scl"]),
        ("DESKBOT_PAN_MIN_DEG", f"{pan['min']}.0f"),
        ("DESKBOT_PAN_MAX_DEG", f"{pan['max']}.0f"),
        ("DESKBOT_PAN_PARK_DEG", f"{pan['park']}.0f"),
        ("DESKBOT_PAN_MAX_SLEW", f"{pan['max_slew_deg_per_sec']}.0f"),
        ("DESKBOT_TILT_MIN_DEG", f"{tilt['min']}.0f"),
        ("DESKBOT_TILT_MAX_DEG", f"{tilt['max']}.0f"),
        ("DESKBOT_TILT_PARK_DEG", f"{tilt['park']}.0f"),
        ("DESKBOT_TILT_MAX_SLEW", f"{tilt['max_slew_deg_per_sec']}.0f"),
        ("DESKBOT_PULSE_MIN_US", s["pulse_min_us"]),
        ("DESKBOT_PULSE_MAX_US", s["pulse_max_us"]),
        ("DESKBOT_DEADMAN_MS", f"{cfg['deadman_timeout_ms']}UL"),
        ("DESKBOT_NOD_MIN", cfg["nod"]["min_times"]),
        ("DESKBOT_NOD_MAX", cfg["nod"]["max_times"]),
        ("DESKBOT_SWEEP_MIN_SPEED", f"{cfg['sweep']['min_speed_deg_s']}.0f"),
        ("DESKBOT_SWEEP_MAX_SPEED", f"{cfg['sweep']['max_speed_deg_s']}.0f"),
        ("DESKBOT_EXPRESSION_COUNT", len(exprs)),
    ]
    lines = [
        "// GENERATED by firmware/tools/gen_deskbot_td.py from device-deskbot.yaml. Do not edit.",
        "#ifndef DESKBOT_GEN_H",
        "#define DESKBOT_GEN_H",
        "",
    ]
    lines += [f"#define {name} {value}" for name, value in defs]
    lines += [
        "",
        "#ifdef DESKBOT_GEN_DEFINE_TD",
        "// Placeholders {device_id} and {broker} are filled in at runtime.",
        f'static const char DESKBOT_TD_TEMPLATE[] = R"TW({td})TW";',
        "#endif",
        "",
        "#endif // DESKBOT_GEN_H",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    """Write deskbot_gen.h; with --check, fail if it is out of date."""
    text = render_header(load_config())
    if "--check" in sys.argv:
        if not HEADER_PATH.exists() or HEADER_PATH.read_text() != text:
            print(f"{HEADER_PATH} is out of date; rerun gen_deskbot_td.py", file=sys.stderr)
            return 1
        return 0
    HEADER_PATH.write_text(text)
    print(f"wrote {HEADER_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
