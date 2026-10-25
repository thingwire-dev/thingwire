"""ThingWire Gateway entry point.

Starts MQTT bridge, discovers devices, compiles tools, and launches MCP server.
Usage: python -m gateway
"""

import asyncio
import logging
import os
import signal
import sys
from typing import Any

from thingwire import __version__
from thingwire.audit_log import AuditLog
from thingwire.config import GatewayConfig
from thingwire.device_roster import DeviceRoster
from thingwire.gateway_runtime import Gateway, build_gateway
from thingwire.mcp_server import create_mcp_server
from thingwire.mqtt_bridge import MqttBridge

logger = logging.getLogger("gateway")

# Components tracked for ordered shutdown
_bridge: MqttBridge | None = None
_audit: AuditLog | None = None
_gateway: Gateway | None = None
_shutdown_tasks: set[asyncio.Task[None]] = set()


async def _shutdown() -> None:
    """Ordered shutdown: MCP stops first, then sweeps and recorder, MQTT, audit log."""
    logger.info("Shutting down...")
    if _gateway:
        await _gateway.stop()
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


def _publish_ha(gw: Gateway, device_id: str) -> None:
    td = gw.bridge.get_td(device_id)
    if td and gw.bridge._client:
        from thingwire.ha_discovery import publish_ha_discovery

        count = publish_ha_discovery(gw.bridge._client, td, device_id)
        logger.info("Published %d HA discovery messages for %s", count, device_id)


async def setup(config: GatewayConfig) -> None:
    """Initialize all components and start the MCP server."""
    global _bridge, _audit, _gateway

    # Initialize audit log
    db_dir = os.path.dirname(config.audit_db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    _audit = AuditLog(config.audit_db_path)
    await _audit.initialize()

    # Load device roster (persisted TDs from previous runs)
    roster = DeviceRoster(config.device_roster_path)
    saved_devices = roster.load()

    # Gateway pieces: safety (persistent e-stop), recorder, pipeline, meta tools
    mcp = create_mcp_server()
    _bridge = MqttBridge(config)
    gw = _gateway = build_gateway(config, mcp, _bridge, _audit)
    loop = asyncio.get_running_loop()
    gw.start_background(loop)  # e-stop topics, telemetry heartbeat, deadman sweep

    # Connect to MQTT
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

    # Home Assistant MQTT discovery
    ha_enabled = os.environ.get("THINGWIRE_HA_DISCOVERY") == "1"

    def _register(device_id: str, raw_td: dict[str, Any]) -> None:
        try:
            tools = gw.register_device(device_id, raw_td)
            logger.info("Registered %d tools for device %s", len(tools), device_id)
        except Exception:
            logger.exception("Failed to register tools for device %s, skipping", device_id)
            return
        if ha_enabled:
            _publish_ha(gw, device_id)

    for device_id in devices:
        _register(device_id, _bridge._devices.get(device_id, {}))

    # Dynamic registration for devices that connect after boot (TD arrives on the MQTT thread)
    def _on_new_device(device_id: str, raw_td: dict[str, Any]) -> None:
        def _apply() -> None:
            _register(device_id, raw_td)
            roster.add_device(device_id, raw_td)

        loop.call_soon_threadsafe(_apply)

    _bridge.on_device_discovered = _on_new_device

    # Start MCP server
    transport = os.environ.get("THINGWIRE_TRANSPORT", "stdio")
    logger.info("Starting MCP server (transport: %s)...", transport)

    try:
        if transport == "sse":
            # sse has no identity layer; create_mcp_server already enforced a loopback host
            host = os.environ.get("THINGWIRE_SSE_HOST") or "127.0.0.1"
            await mcp.run_http_async(transport="sse", host=host)
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
