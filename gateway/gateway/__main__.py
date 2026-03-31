"""ThingWire Gateway entry point.

Starts MQTT bridge, discovers devices, compiles tools, and launches MCP server.
Usage: python -m gateway
"""

import asyncio
import logging
import os
import sys

from gateway.audit_log import AuditLog
from gateway.config import GatewayConfig
from gateway.mcp_server import create_mcp_server, register_device_tools, register_meta_tools
from gateway.mqtt_bridge import MqttBridge
from gateway.safety import SafetyLayer

logger = logging.getLogger("gateway")


async def setup(config: GatewayConfig) -> None:
    """Initialize all components and start the MCP server."""
    # Initialize audit log
    os.makedirs(os.path.dirname(config.audit_db_path) or ".", exist_ok=True)
    audit = AuditLog(config.audit_db_path)
    await audit.initialize()

    # Initialize safety layer
    safety = SafetyLayer(config.safety_config_path)

    # Connect to MQTT
    bridge = MqttBridge(config)
    logger.info("Connecting to MQTT broker at %s:%d...", config.mqtt_broker, config.mqtt_port)
    await bridge.connect()

    # Wait for device discovery
    logger.info("Waiting for device discovery (30s timeout)...")
    devices = await bridge.wait_for_devices(timeout=30.0)

    if not devices:
        logger.warning("No devices discovered. MCP server will start with meta-tools only.")

    # Create MCP server
    mcp = create_mcp_server()

    # Register device tools
    for device_id in devices:
        td = bridge.get_td(device_id)
        if td:
            raw_td = bridge._devices.get(device_id, {})
            safety.register_device(device_id, raw_td)
            tools = register_device_tools(mcp, td, bridge, safety, audit)
            logger.info("Registered %d tools for device %s", len(tools), device_id)

    # Register meta-tools
    register_meta_tools(mcp, bridge, audit)

    # Start MCP server
    transport = os.environ.get("THINGWIRE_TRANSPORT", "stdio")
    logger.info("Starting MCP server (transport: %s)...", transport)

    if transport == "sse":
        await mcp.run_sse_async()
    else:
        await mcp.run_async(transport="stdio")


def main() -> None:
    """Main entry point."""
    config = GatewayConfig()

    logging.basicConfig(
        level=getattr(logging, config.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    logger.info("ThingWire Gateway starting...")
    logger.info("Config: broker=%s:%d", config.mqtt_broker, config.mqtt_port)

    try:
        asyncio.run(setup(config))
    except KeyboardInterrupt:
        logger.info("Gateway shutting down.")
    except ConnectionError as e:
        logger.error("Failed to connect: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
