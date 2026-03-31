"""ThingWire CLI — serve, init, and device management."""

import logging
import os
import sys

import click

from thingwire import __version__

DOCKER_COMPOSE_TEMPLATE = """\
services:
  mosquitto:
    image: eclipse-mosquitto:2
    ports:
      - "1883:1883"
    volumes:
      - ./mosquitto.conf:/mosquitto/config/mosquitto.conf
      - mosquitto-data:/mosquitto/data
    restart: unless-stopped

volumes:
  mosquitto-data:
"""

MOSQUITTO_CONF_TEMPLATE = """\
listener 1883
allow_anonymous true
persistence true
persistence_location /mosquitto/data/
log_dest stdout
"""

SAFETY_CONFIG_TEMPLATE = """\
# ThingWire Safety Configuration
# Devices are auto-registered from their WoT TD on discovery.

devices: {}

global:
  require_confirmation_for_dangerous: true
  deadman_switch_timeout_seconds: 300
  audit_log_retention_days: 30
  default_rate_limit:
    max_calls: 10
    window_seconds: 60
"""


@click.group()
@click.version_option(version=__version__, prog_name="thingwire")
def main() -> None:
    """ThingWire — AI-to-hardware gateway. WoT TD to MCP tools."""


@main.command()
@click.option("--broker", default=None, help="MQTT broker host (default: localhost)")
@click.option("--port", default=None, type=int, help="MQTT broker port (default: 1883)")
@click.option(
    "--transport",
    default="stdio",
    type=click.Choice(["stdio", "sse"]),
    help="MCP transport",
)
@click.option(
    "--log-level",
    default="INFO",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]),
)
def serve(broker: str | None, port: int | None, transport: str, log_level: str) -> None:
    """Start the ThingWire gateway and MCP server."""
    import asyncio

    if broker:
        os.environ["THINGWIRE_MQTT_BROKER"] = broker
    if port:
        os.environ["THINGWIRE_MQTT_PORT"] = str(port)
    os.environ["THINGWIRE_LOG_LEVEL"] = log_level
    os.environ["THINGWIRE_TRANSPORT"] = transport

    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    logger = logging.getLogger("thingwire")
    logger.info("ThingWire v%s starting...", __version__)

    from thingwire.__main__ import setup
    from thingwire.config import GatewayConfig

    config = GatewayConfig()

    try:
        asyncio.run(setup(config))
    except KeyboardInterrupt:
        logger.info("Gateway interrupted.")
    except ConnectionError as e:
        logger.error("Failed to connect: %s", e)
        sys.exit(1)


@main.command()
def init() -> None:
    """Initialize a ThingWire project in the current directory."""
    files = {
        "docker-compose.yml": DOCKER_COMPOSE_TEMPLATE,
        "mosquitto.conf": MOSQUITTO_CONF_TEMPLATE,
        "safety_config.yaml": SAFETY_CONFIG_TEMPLATE,
    }

    for filename, content in files.items():
        if os.path.exists(filename):
            click.echo(f"  exists  {filename}")
        else:
            with open(filename, "w") as f:
                f.write(content)
            click.echo(f"  created {filename}")

    click.echo("\nReady! Next steps:")
    click.echo("  1. docker compose up -d")
    click.echo("  2. thingwire serve")


@main.command()
@click.option("--broker", default="localhost", help="MQTT broker host")
@click.option("--port", default=1883, type=int, help="MQTT broker port")
@click.option("--timeout", default=5.0, type=float, help="Discovery timeout in seconds")
def devices(broker: str, port: int, timeout: float) -> None:
    """Discover devices on the MQTT broker."""
    import asyncio

    from thingwire.config import GatewayConfig
    from thingwire.mqtt_bridge import MqttBridge

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    config = GatewayConfig(mqtt_broker=broker, mqtt_port=port)

    async def _discover() -> None:
        bridge = MqttBridge(config)
        try:
            await bridge.connect()
        except ConnectionError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)

        click.echo(f"Listening for devices on {broker}:{port} ({timeout}s)...")
        found = await bridge.wait_for_devices(timeout=timeout)

        if not found:
            click.echo("No devices found.")
        else:
            for device_id in found:
                td = bridge.get_td(device_id)
                title = td.title if td else "unknown"
                status = bridge.get_device_status(device_id)
                props = len(td.properties) if td else 0
                actions = len(td.actions) if td else 0
                click.echo(
                    f"  {device_id}  {title}  [{status}]  {props} properties, {actions} actions"
                )

        await bridge.disconnect()

    asyncio.run(_discover())


@main.command()
@click.argument("td_file", type=click.Path(exists=True))
def validate(td_file: str) -> None:
    """Validate a WoT Thing Description JSON file."""
    from thingwire.td_loader import parse_thing_description

    with open(td_file) as f:
        raw = f.read()

    try:
        td = parse_thing_description(raw)
    except ValueError as e:
        click.echo(f"INVALID: {e}", err=True)
        sys.exit(1)

    click.echo(f"VALID: {td.title}")
    click.echo(f"  ID: {td.id}")
    click.echo(f"  Properties: {len(td.properties)} ({', '.join(td.properties)})")
    click.echo(f"  Actions: {len(td.actions)} ({', '.join(td.actions)})")


@main.command(name="export")
@click.argument("td_file", type=click.Path(exists=True))
@click.option(
    "--format",
    "fmt",
    default="openai",
    type=click.Choice(["openai", "mcp"]),
    help="Output format",
)
@click.option("--prefix", default=None, help="Device prefix for tool names")
def export_tools(td_file: str, fmt: str, prefix: str | None) -> None:
    """Export WoT TD as AI tool definitions (OpenAI or MCP format)."""
    import json as json_mod

    from thingwire.td_loader import parse_thing_description
    from thingwire.tool_compiler import compile_tools, export_openai_tools

    with open(td_file) as f:
        raw = f.read()

    try:
        td = parse_thing_description(raw)
    except ValueError as e:
        click.echo(f"Invalid TD: {e}", err=True)
        sys.exit(1)

    if fmt == "openai":
        tools = export_openai_tools(td, device_prefix=prefix)
    else:
        compiled = compile_tools(td, device_prefix=prefix)
        tools = [t.model_dump() for t in compiled]

    click.echo(json_mod.dumps(tools, indent=2))


@main.command()
@click.option("--device-id", default="thingwire-demo-001", help="Device ID")
@click.option("--broker", default="localhost", help="MQTT broker host")
@click.option("--port", default=1883, type=int, help="MQTT broker port")
def sim(device_id: str, broker: str, port: int) -> None:
    """Run a virtual device simulator (no hardware needed)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    # Import the virtual device module from scripts/
    # If installed via pip, we bundle a simple inline simulator
    import json as json_mod
    import random
    import time
    import uuid
    from datetime import UTC, datetime

    try:
        import paho.mqtt.client as paho_mqtt
    except ImportError:
        click.echo("Error: paho-mqtt required. pip install paho-mqtt", err=True)
        sys.exit(1)

    from thingwire.td_loader import ThingDescription

    logger = logging.getLogger("thingwire.sim")

    # Build TD
    td_dict = {
        "@context": "https://www.w3.org/2019/wot/td/v1.1",
        "@type": "Thing",
        "id": f"urn:thingwire:device:{device_id}",
        "title": "ThingWire Virtual Device",
        "description": "Simulated ESP32-S3 with temperature, humidity, motion sensors and relay",
        "securityDefinitions": {"nosec_sc": {"scheme": "nosec"}},
        "security": ["nosec_sc"],
        "properties": {
            "temperature": {
                "type": "number", "unit": "celsius", "readOnly": True,
                "description": "Simulated temperature reading",
                "forms": [{"href": f"mqtt://{broker}/thingwire/{device_id}/telemetry", "op": "observeproperty"}],
            },
            "humidity": {
                "type": "number", "unit": "percent", "readOnly": True,
                "description": "Simulated humidity reading",
                "forms": [{"href": f"mqtt://{broker}/thingwire/{device_id}/telemetry", "op": "observeproperty"}],
            },
            "motion": {
                "type": "boolean", "readOnly": True,
                "description": "Simulated PIR motion sensor",
                "forms": [{"href": f"mqtt://{broker}/thingwire/{device_id}/telemetry", "op": "observeproperty"}],
            },
        },
        "actions": {
            "setRelay": {
                "title": "Control relay",
                "description": "Turn relay on or off",
                "input": {
                    "type": "object",
                    "properties": {"state": {"type": "boolean", "description": "true = on, false = off"}},
                    "required": ["state"],
                },
                "safe": False, "idempotent": True,
                "forms": [{"href": f"mqtt://{broker}/thingwire/{device_id}/command", "op": "invokeaction"}],
            }
        },
    }

    prefix = f"thingwire/{device_id}"
    relay_state = False

    def on_connect(client: paho_mqtt.Client, _ud: object, _fl: object, rc: int, _p: object = None) -> None:
        if rc != 0:
            logger.error("Connection failed: rc=%d", rc)
            return
        logger.info("Connected to %s:%d as %s", broker, port, device_id)
        client.publish(f"{prefix}/td", json_mod.dumps(td_dict), retain=True)
        client.publish(f"{prefix}/status", "online", retain=True)
        client.subscribe(f"{prefix}/command")

    def on_message(client: paho_mqtt.Client, _ud: object, msg: paho_mqtt.MQTTMessage) -> None:
        nonlocal relay_state
        try:
            cmd = json_mod.loads(msg.payload)
            if cmd.get("target") == "relay1" and cmd.get("command") == "set":
                relay_state = bool(cmd.get("value"))
                logger.info("Relay → %s", "ON" if relay_state else "OFF")
                ack = {"action_id": cmd.get("action_id", ""), "status": "ok", "value": relay_state}
                client.publish(f"{prefix}/status", json_mod.dumps(ack))
        except Exception:
            logger.exception("Bad command")

    client = paho_mqtt.Client(
        callback_api_version=paho_mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"sim-{device_id}",
    )
    client.will_set(f"{prefix}/status", "offline", retain=True)
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(broker, port)
    except OSError as e:
        click.echo(f"Error: cannot connect to {broker}:{port} — {e}", err=True)
        sys.exit(1)

    client.loop_start()
    click.echo(f"Virtual device {device_id} running on {broker}:{port}. Ctrl+C to stop.")

    try:
        while True:
            telemetry = {
                "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "readings": {
                    "temp1": {"value": round(22.0 + random.uniform(-2, 2), 1), "unit": "celsius"},
                    "humidity1": {"value": round(45.0 + random.uniform(-5, 5), 1), "unit": "percent"},
                    "motion1": {"value": random.random() < 0.3, "unit": "boolean"},
                },
            }
            client.publish(f"{prefix}/telemetry", json_mod.dumps(telemetry))
            time.sleep(5)
    except KeyboardInterrupt:
        click.echo("\nShutting down virtual device.")
    finally:
        client.publish(f"{prefix}/status", "offline", retain=True)
        client.loop_stop()
        client.disconnect()
