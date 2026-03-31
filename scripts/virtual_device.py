#!/usr/bin/env python3
"""ThingWire Virtual Device Simulator.

Simulates an ESP32-S3 device on MQTT — publishes WoT TD, telemetry,
and responds to commands. Unblocks gateway development without hardware.
"""

import argparse
import json
import logging
import random
import time
import uuid
from datetime import UTC, datetime

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)

# Simulated sensor state
_relay_state = False
_base_temp = 22.0
_base_humidity = 45.0


def build_thing_description(device_id: str, broker: str) -> dict:
    """Build a W3C WoT Thing Description for the virtual device."""
    return {
        "@context": "https://www.w3.org/2019/wot/td/v1.1",
        "@type": "Thing",
        "id": f"urn:thingwire:device:{device_id}",
        "title": "ThingWire Demo Device",
        "description": "ESP32-S3 with temperature, humidity, motion sensors and relay actuator",
        "securityDefinitions": {"nosec_sc": {"scheme": "nosec"}},
        "security": ["nosec_sc"],
        "properties": {
            "temperature": {
                "type": "number",
                "unit": "celsius",
                "readOnly": True,
                "description": "Current temperature reading from DHT22 sensor",
                "forms": [
                    {
                        "href": f"mqtt://{broker}/thingwire/{device_id}/telemetry",
                        "op": "observeproperty",
                    }
                ],
            },
            "humidity": {
                "type": "number",
                "unit": "percent",
                "readOnly": True,
                "description": "Current humidity reading from DHT22 sensor",
                "forms": [
                    {
                        "href": f"mqtt://{broker}/thingwire/{device_id}/telemetry",
                        "op": "observeproperty",
                    }
                ],
            },
            "motion": {
                "type": "boolean",
                "readOnly": True,
                "description": "PIR motion sensor \u2014 true when motion detected",
                "forms": [
                    {
                        "href": f"mqtt://{broker}/thingwire/{device_id}/telemetry",
                        "op": "observeproperty",
                    }
                ],
            },
        },
        "actions": {
            "setRelay": {
                "title": "Control relay",
                "description": "Turn relay on or off. Controls a physical device connected to the relay.",
                "input": {
                    "type": "object",
                    "properties": {
                        "state": {"type": "boolean", "description": "true = on, false = off"}
                    },
                    "required": ["state"],
                },
                "safe": False,
                "idempotent": True,
                "forms": [
                    {
                        "href": f"mqtt://{broker}/thingwire/{device_id}/command",
                        "op": "invokeaction",
                    }
                ],
            }
        },
    }


def generate_telemetry() -> dict:
    """Generate realistic fake telemetry readings."""
    return {
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "readings": {
            "temp1": {
                "value": round(_base_temp + random.uniform(-2.0, 2.0), 1),
                "unit": "celsius",
            },
            "humidity1": {
                "value": round(_base_humidity + random.uniform(-5.0, 5.0), 1),
                "unit": "percent",
            },
            "motion1": {
                "value": random.random() < 0.3,
                "unit": "boolean",
            },
        },
    }


def handle_command(payload: bytes, device_id: str, client: mqtt.Client) -> None:
    """Process incoming command and publish ack."""
    global _relay_state  # noqa: PLW0603

    try:
        cmd = json.loads(payload)
    except json.JSONDecodeError:
        logger.error("Malformed command JSON: %s", payload)
        return

    action_id = cmd.get("action_id", str(uuid.uuid4()))
    target = cmd.get("target", "unknown")
    command = cmd.get("command", "unknown")
    value = cmd.get("value")

    logger.info(
        "Command received: action_id=%s target=%s command=%s value=%s",
        action_id,
        target,
        command,
        value,
    )

    if target == "relay1" and command == "set":
        _relay_state = bool(value)
        ack = {
            "action_id": action_id,
            "status": "ok",
            "target": target,
            "command": command,
            "value": _relay_state,
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        logger.info("Relay state set to: %s", _relay_state)
    else:
        ack = {
            "action_id": action_id,
            "status": "error",
            "error": f"Unknown target/command: {target}/{command}",
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        logger.warning("Unknown command: target=%s command=%s", target, command)

    client.publish(
        f"thingwire/{device_id}/status",
        json.dumps(ack),
    )


def run(device_id: str, broker: str, port: int) -> None:
    """Run the virtual device simulator."""
    topic_prefix = f"thingwire/{device_id}"

    def on_connect(client: mqtt.Client, _userdata: object, _flags: object, rc: int, _props: object = None) -> None:
        if rc != 0:
            logger.error("Connection failed with code %d", rc)
            return

        logger.info("Connected to MQTT broker at %s:%d", broker, port)

        # Publish WoT TD (retained)
        td = build_thing_description(device_id, broker)
        client.publish(f"{topic_prefix}/td", json.dumps(td), retain=True)
        logger.info("Published WoT TD to %s/td", topic_prefix)

        # Publish online status
        client.publish(f"{topic_prefix}/status", "online", retain=True)

        # Subscribe to commands
        client.subscribe(f"{topic_prefix}/command")
        logger.info("Subscribed to %s/command", topic_prefix)

    def on_message(client: mqtt.Client, _userdata: object, msg: mqtt.MQTTMessage) -> None:
        if msg.topic == f"{topic_prefix}/command":
            handle_command(msg.payload, device_id, client)

    def on_disconnect(_client: mqtt.Client, _userdata: object, rc: int, _props: object = None) -> None:
        if rc != 0:
            logger.warning("Unexpected disconnect (rc=%d), will auto-reconnect", rc)

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"virtual-{device_id}",
    )

    # Set LWT (Last Will and Testament)
    client.will_set(f"{topic_prefix}/status", "offline", retain=True)

    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    logger.info("Connecting to %s:%d as device %s...", broker, port, device_id)
    client.connect(broker, port)
    client.loop_start()

    try:
        while True:
            telemetry = generate_telemetry()
            client.publish(
                f"{topic_prefix}/telemetry",
                json.dumps(telemetry),
            )
            logger.debug("Published telemetry: %s", json.dumps(telemetry, indent=2))
            time.sleep(5)
    except KeyboardInterrupt:
        logger.info("Shutting down virtual device %s", device_id)
    finally:
        client.publish(f"{topic_prefix}/status", "offline", retain=True)
        client.loop_stop()
        client.disconnect()


def main() -> None:
    """Parse CLI args and start the virtual device."""
    parser = argparse.ArgumentParser(
        description="ThingWire Virtual Device Simulator",
    )
    parser.add_argument(
        "--device-id",
        default="thingwire-demo-001",
        help="Device ID (default: thingwire-demo-001)",
    )
    parser.add_argument(
        "--broker",
        default="localhost",
        help="MQTT broker host (default: localhost)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=1883,
        help="MQTT broker port (default: 1883)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: INFO)",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    run(args.device_id, args.broker, args.port)


if __name__ == "__main__":
    main()
