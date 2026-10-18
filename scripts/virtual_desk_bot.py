#!/usr/bin/env python3
"""ThingWire virtual desk bot.

Simulates the desk robot (two-axis head, OLED eyes, presence sensor) on MQTT:
retained TD, 10 Hz telemetry, slew-limited head motion, and the look_at,
set_expression, nod and sweep commands. Also publishes the fake camera scene.
"""

import argparse
import json
import logging
import math
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desk_bot_estop import EstopMixin

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
TD_PATH = ROOT / "td-library" / "desk-bot.td.json"
SCENE_PATH = Path(__file__).resolve().parent / "scene.yaml"

PAN_LIMIT = 90.0
TILT_LIMIT = 30.0
DEFAULT_SLEW = 90.0
TELEMETRY_HZ = 10.0
EXPRESSIONS = ("neutral", "happy", "curious", "sleepy", "surprised", "alert")
NOD_AMPLITUDE = 10.0


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def build_td(device_id: str, broker: str) -> dict[str, Any]:
    """Load the desk bot TD template and fill in device id and broker."""
    raw = TD_PATH.read_text().replace("{device_id}", device_id).replace("{broker}", broker)
    return json.loads(raw)  # type: ignore[no-any-return]


def load_scene(path: Path = SCENE_PATH) -> dict[str, Any]:
    """Load the fake camera scene YAML (empty scene if the file is missing)."""
    if not path.exists():
        return {"camera": {"fov_deg": 60}, "people": [], "signs": []}
    return yaml.safe_load(path.read_text()) or {}  # type: ignore[no-any-return]


def _clamp_step(current: float, target: float, max_delta: float) -> float:
    """Move current toward target by at most max_delta."""
    delta = target - current
    return target if abs(delta) <= max_delta else current + math.copysign(max_delta, delta)


class DeskBot(EstopMixin):
    """Pure simulation state: head pose, expression, scene. No MQTT here."""

    def __init__(self, scene: dict[str, Any] | None = None, max_slew: float = DEFAULT_SLEW) -> None:
        self.pan = 0.0
        self.tilt = 0.0
        self.expression = "neutral"
        self.max_slew = max_slew
        self.scene = scene or {"camera": {"fov_deg": 60}, "people": [], "signs": []}
        self._target = (0.0, 0.0)
        self._waypoints: list[tuple[float, float]] = []
        self._sweep_speed: float | None = None
        self._sweep_dir = 1.0

    # --- motion ---------------------------------------------------------

    def step(self, dt: float) -> None:
        """Advance the simulation by dt seconds, never exceeding the slew limit."""
        if self._sweep_speed is not None:
            edge = PAN_LIMIT * self._sweep_dir
            self._target = (edge, self.tilt)
            if abs(self.pan - edge) < 1e-9:
                self._sweep_dir *= -1.0
        elif self._waypoints and self._at_target():
            self._target = self._waypoints.pop(0)
        speed = min(self._sweep_speed or self.max_slew, self.max_slew)
        limit = speed * dt
        self.pan = _clamp_step(self.pan, self._target[0], limit)
        self.tilt = _clamp_step(self.tilt, self._target[1], limit)

    def _at_target(self) -> bool:
        return abs(self.pan - self._target[0]) < 1e-9 and abs(self.tilt - self._target[1]) < 1e-9

    # --- scene ----------------------------------------------------------
    def _in_view(self, pan_deg: float) -> bool:
        fov = float(self.scene.get("camera", {}).get("fov_deg", 60))
        return abs(pan_deg - self.pan) <= fov / 2

    def visible_people(self) -> list[dict[str, Any]]:
        """People currently inside the camera field of view."""
        return [p for p in self.scene.get("people", []) if self._in_view(float(p["pan_deg"]))]

    def visible_text(self) -> list[str]:
        """Sign text currently inside the camera field of view."""
        return [
            s["text"] for s in self.scene.get("signs", []) if self._in_view(float(s["pan_deg"]))
        ]

    # --- outputs --------------------------------------------------------

    def telemetry(self) -> dict[str, Any]:
        """Build a telemetry message keyed by TD property name."""
        return {
            "timestamp": _now(),
            "readings": {
                "head_pan": {"value": round(self.pan, 2), "unit": "degrees"},
                "head_tilt": {"value": round(self.tilt, 2), "unit": "degrees"},
                "expression": {"value": self.expression, "unit": "enum"},
                "person_detected": {"value": bool(self.visible_people()), "unit": "boolean"},
            },
        }

    def scene_view(self) -> dict[str, Any]:
        """What the fake camera sees right now."""
        return {
            "timestamp": _now(),
            "people": self.visible_people(),
            "visible_text": self.visible_text(),
        }

    # --- commands -------------------------------------------------------

    def apply_command(self, cmd: dict[str, Any]) -> dict[str, Any]:
        """Apply a command message and return the ack payload."""
        action_id = cmd.get("action_id", str(uuid.uuid4()))
        if self.estopped:
            return self.estop_ack(action_id, _now())
        name = self._action_name(cmd)
        value = cmd.get("value")
        args = value if isinstance(value, dict) else {}
        try:
            handler = {
                "look_at": self._look_at,
                "set_expression": self._set_expression,
                "nod": self._nod,
                "sweep": self._sweep,
            }[name]
            handler(args)
        except KeyError as exc:
            if exc.args and exc.args[0] == name:
                return self._ack(action_id, "error", error=f"Unknown action: {name}")
            return self._ack(action_id, "error", error=f"Missing argument: {exc.args[0]}")
        except (ValueError, TypeError) as exc:
            return self._ack(action_id, "error", error=str(exc))
        return self._ack(action_id, "ok", action=name)

    @staticmethod
    def _action_name(cmd: dict[str, Any]) -> str:
        known = ("look_at", "set_expression", "nod", "sweep")
        for candidate in (cmd.get("command"), cmd.get("action")):
            if candidate in known:
                return str(candidate)
        target = str(cmd.get("target", "")).removesuffix("1")
        for name in known:
            if target in (name, name.removeprefix("set")):
                return name
        return str(cmd.get("command", target))

    @staticmethod
    def _ack(action_id: str, status: str, **extra: Any) -> dict[str, Any]:
        return {"action_id": action_id, "status": status, "timestamp": _now(), **extra}

    def _look_at(self, args: dict[str, Any]) -> None:
        pan, tilt = float(args["pan"]), float(args["tilt"])
        if abs(pan) > PAN_LIMIT or abs(tilt) > TILT_LIMIT:
            raise ValueError(f"look_at out of range: pan={pan} tilt={tilt}")
        self._sweep_speed = None
        self._waypoints.clear()
        self._target = (pan, tilt)

    def _set_expression(self, args: dict[str, Any]) -> None:
        expression = str(args["expression"])
        if expression not in EXPRESSIONS:
            raise ValueError(f"Unknown expression: {expression}")
        self.expression = expression

    def _nod(self, args: dict[str, Any]) -> None:
        times = int(args["times"])
        if not 1 <= times <= 3:
            raise ValueError("nod times must be 1 to 3")
        base = self._target
        self._sweep_speed = None
        self._waypoints = [
            (base[0], max(-TILT_LIMIT, base[1] - NOD_AMPLITUDE)),
            (base[0], min(TILT_LIMIT, base[1] + NOD_AMPLITUDE / 2)),
        ] * times + [base]
        self._target = self._waypoints.pop(0)

    def _sweep(self, args: dict[str, Any]) -> None:
        speed = float(args["speed_deg_s"])
        if not 10 <= speed <= 120:
            raise ValueError("sweep speed_deg_s must be 10 to 120")
        self._waypoints.clear()
        self._sweep_speed = speed
        self._sweep_dir = 1.0


def run(device_id: str, broker: str, port: int, scene_path: Path) -> None:
    """Connect to MQTT and run the bot loop until interrupted."""
    prefix = f"thingwire/{device_id}"
    bot = DeskBot(load_scene(scene_path))
    lock = threading.Lock()

    def on_connect(
        client: mqtt.Client, _ud: object, _flags: object, rc: int, _p: object = None
    ) -> None:
        if rc != 0:
            logger.error("Connection failed with code %d", rc)
            return
        client.publish(f"{prefix}/td", json.dumps(build_td(device_id, broker)), retain=True)
        client.publish(f"{prefix}/status", "online", retain=True)
        client.subscribe(f"{prefix}/command")
        client.subscribe(f"{prefix}/estop")
        client.subscribe("thingwire/estop")
        logger.info("Desk bot %s online", device_id)

    def on_message(client: mqtt.Client, _ud: object, msg: mqtt.MQTTMessage) -> None:
        if msg.topic.endswith("/estop"):
            with lock:
                logger.warning("E-stop %s", bot.handle_estop_payload(bytes(msg.payload)))
            return
        try:
            cmd = json.loads(msg.payload)
        except json.JSONDecodeError:
            logger.error("Malformed command JSON: %r", msg.payload)
            return
        with lock:
            ack = bot.apply_command(cmd)
        logger.info("Command %s -> %s", cmd, ack["status"])
        client.publish(f"{prefix}/status", json.dumps(ack))

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id=f"virtual-{device_id}"
    )
    client.will_set(f"{prefix}/status", "offline", retain=True)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(broker, port)
    client.loop_start()

    dt = 1.0 / TELEMETRY_HZ
    seen = False
    try:
        while True:
            with lock:
                bot.step(dt)
                telemetry, view = bot.telemetry(), bot.scene_view()
            client.publish(f"{prefix}/telemetry", json.dumps(telemetry))
            client.publish(f"{prefix}/scene", json.dumps(view))
            present = bool(view["people"])
            if present and not seen:
                event = {
                    "event": "person_seen",
                    "confidence": view["people"][0].get("confidence", 0.9),
                }
                client.publish(f"{prefix}/event", json.dumps(event))
            seen = present
            time.sleep(dt)
    except KeyboardInterrupt:
        logger.info("Shutting down desk bot %s", device_id)
    finally:
        client.publish(f"{prefix}/status", "offline", retain=True)
        client.loop_stop()
        client.disconnect()


def main() -> None:
    """Parse CLI args and start the virtual desk bot."""
    parser = argparse.ArgumentParser(description="ThingWire virtual desk bot")
    parser.add_argument("--device-id", default="deskbot-001")
    parser.add_argument("--broker", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--scene", type=Path, default=SCENE_PATH)
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    run(args.device_id, args.broker, args.port, args.scene)


if __name__ == "__main__":
    main()
