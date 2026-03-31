"""ThingWire Gateway entry point.

Starts MQTT bridge, discovers devices, compiles tools, and launches MCP server.
Usage: python -m gateway
"""

import asyncio
import logging
import os
import signal
import sys

from thingwire import __version__
from thingwire.audit_log import AuditLog
from thingwire.config import GatewayConfig
from thingwire.mcp_server import create_mcp_server, register_device_tools, register_meta_tools
from thingwire.mqtt_bridge import MqttBridge
from thingwire.safety import SafetyLayer

logger = logging.getLogger("gateway")

# Components tracked for ordered shutdown
_bridge: MqttBridge | None = None
_audit: AuditLog | None = None
_shutdown_tasks: set[asyncio.Task[None]] = set()


async def _shutdown() -> None:
    """Ordered shutdown: MCP stops first, then MQTT, then audit log."""
    logger.info("Shutting down...")
    if _bridge:
        await _bridge.disconnect()
    if _audit:
        await _audit.close()
    logger.info("Shutdown complete.")


def _handle_signal(sig: int, _frame: object) -> None:
    """Handle SIGINT/SIGTERM for clean Docker stop."""
    logger.info("Received signal %d, initiating shutdown", sig)
    loop = asyncio.get_event_loop()
    if loop.is_running():
        task = loop.create_task(_shutdown())
        _shutdown_tasks.add(task)
        task.add_done_callback(_shutdown_tasks.discard)


async def setup(config: GatewayConfig) -> None:
    """Initialize all components and start the MCP server."""
    global _bridge, _audit

    # Initialize audit log
    db_dir = os.path.dirname(config.audit_db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    _audit = AuditLog(config.audit_db_path)
    await _audit.initialize()

    # Initialize safety layer
    safety = SafetyLayer(config.safety_config_path)

    # Load device roster (persisted TDs from previous runs)
    from thingwire.device_roster import DeviceRoster

    roster = DeviceRoster(config.device_roster_path)
    saved_devices = roster.load()

    # Connect to MQTT
    _bridge = MqttBridge(config)
    logger.info("Connecting to MQTT broker at %s:%d...", config.mqtt_broker, config.mqtt_port)
    await _bridge.connect()

    # Pre-populate bridge with saved devices
    if saved_devices:
        for device_id, td_dict in saved_devices.items():
            _bridge._devices[device_id] = td_dict
        logger.info("Restored %d device(s) from roster", len(saved_devices))

    # Wait for device discovery (may find new ones or refresh existing)
    logger.info("Waiting for device discovery (%.0fs timeout)...", config.device_discovery_timeout)
    devices = await _bridge.wait_for_devices(timeout=config.device_discovery_timeout)

    # Save updated roster
    roster.save(_bridge._devices)

    if not devices:
        logger.warning("No devices discovered. MCP server will start with meta-tools only.")

    # Create MCP server
    mcp = create_mcp_server()

    # Home Assistant MQTT discovery
    ha_enabled = os.environ.get("THINGWIRE_HA_DISCOVERY") == "1"

    # Register device tools
    for device_id in devices:
        td = _bridge.get_td(device_id)
        if td:
            raw_td = _bridge._devices.get(device_id, {})
            safety.register_device(device_id, raw_td)
            try:
                tools = register_device_tools(mcp, td, _bridge, safety, _audit)
                logger.info("Registered %d tools for device %s", len(tools), device_id)
            except Exception:
                logger.exception("Failed to register tools for device %s, skipping", device_id)

            if ha_enabled and _bridge._client:
                from thingwire.ha_discovery import publish_ha_discovery

                count = publish_ha_discovery(_bridge._client, td, device_id)
                logger.info("Published %d HA discovery messages for %s", count, device_id)

    # Dynamic registration for devices that connect after boot
    def _on_new_device(device_id: str, raw_td: dict) -> None:  # type: ignore[type-arg]
        td = _bridge.get_td(device_id)
        if not td:
            return
        safety.register_device(device_id, raw_td)
        try:
            tools = register_device_tools(mcp, td, _bridge, safety, _audit)
            logger.info("Dynamically registered %d tools for %s", len(tools), device_id)
        except Exception:
            logger.exception("Failed to dynamically register %s", device_id)

        if ha_enabled and _bridge._client:
            from thingwire.ha_discovery import publish_ha_discovery

            publish_ha_discovery(_bridge._client, td, device_id)

        roster.add_device(device_id, raw_td)

    _bridge.on_device_discovered = _on_new_device

    # Register meta-tools
    register_meta_tools(mcp, _bridge, _audit)

    # Start MCP server
    transport = os.environ.get("THINGWIRE_TRANSPORT", "stdio")
    logger.info("Starting MCP server (transport: %s)...", transport)

    try:
        if transport == "sse":
            await mcp.run_sse_async()
        else:
            await mcp.run_async(transport="stdio")
    finally:
        await _shutdown()


def _setup_logging(config: GatewayConfig) -> None:
    """Configure logging based on config."""
    if config.log_format == "json":
        # Structured JSON logging for Docker/production
        fmt = '{"time":"%(asctime)s","name":"%(name)s","level":"%(levelname)s","msg":"%(message)s"}'
    else:
        fmt = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"

    logging.basicConfig(
        level=getattr(logging, config.log_level),
        format=fmt,
        stream=sys.stderr,
    )


def main() -> None:
    """Main entry point."""
    try:
        config = GatewayConfig()
    except Exception as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    _setup_logging(config)

    logger.info("ThingWire Gateway v%s starting...", __version__)
    logger.info("Config: broker=%s:%d", config.mqtt_broker, config.mqtt_port)

    # Register signal handlers for clean Docker stop
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        asyncio.run(setup(config))
    except KeyboardInterrupt:
        logger.info("Gateway interrupted.")
    except ConnectionError as e:
        logger.error("Failed to connect: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
