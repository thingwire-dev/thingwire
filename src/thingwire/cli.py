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
