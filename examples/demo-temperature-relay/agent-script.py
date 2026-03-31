#!/usr/bin/env python3
"""Demo: Temperature-based relay control via ThingWire MCP.

Shows how an agent script can read sensors and control actuators
through the ThingWire gateway's MCP tools.

Usage:
    # Start mosquitto + virtual device + gateway first, then:
    python3.11 examples/demo-temperature-relay/agent-script.py
"""

import asyncio
import json
import logging
import sys

sys.path.insert(0, "gateway")

from gateway.audit_log import AuditLog
from gateway.config import GatewayConfig
from gateway.mqtt_bridge import MqttBridge
from gateway.safety import SafetyLayer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("demo")

TEMPERATURE_THRESHOLD = 28.0


async def run_demo() -> None:
    """Run the temperature → relay demo."""
    config = GatewayConfig()
    bridge = MqttBridge(config)
    safety = SafetyLayer(config.safety_config_path)
    audit = AuditLog(":memory:")
    await audit.initialize()

    logger.info("Connecting to MQTT broker...")
    await bridge.connect()

    logger.info("Waiting for device discovery...")
    devices = await bridge.wait_for_devices(timeout=15.0)
    if not devices:
        logger.error("No devices found. Is the virtual device running?")
        return

    device_id = devices[0]
    logger.info("Found device: %s", device_id)

    # Register safety rules
    raw_td = bridge._devices.get(device_id, {})
    safety.register_device(device_id, raw_td)

    # Wait for telemetry
    logger.info("Waiting for telemetry data...")
    await asyncio.sleep(6)

    # Read temperature
    reading = bridge.get_latest_reading(device_id, "temp1")
    if "error" in reading:
        logger.error("Could not read temperature: %s", reading["error"])
        return

    temp = reading["value"]
    logger.info("Current temperature: %.1f°C", temp)

    # Agent reasoning
    if temp > TEMPERATURE_THRESHOLD:
        logger.info(
            "Temperature %.1f°C exceeds threshold %.1f°C — turning ON relay (fan)",
            temp,
            TEMPERATURE_THRESHOLD,
        )

        # Safety checks
        safety.check_permission(device_id, "do_setRelay")
        safety.check_rate_limit(device_id, "do_setRelay")
        confirmation = safety.require_confirmation("do_setRelay", {"state": True})
        logger.info("Safety: %s", confirmation)

        # Send command
        action_id = await bridge.send_command(device_id, "relay1", "set", True)
        logger.info("Command sent (action_id: %s)", action_id)

        # Audit
        await audit.record(device_id, "do_setRelay", {"state": True}, {"action_id": action_id})
    else:
        logger.info(
            "Temperature %.1f°C is below threshold %.1f°C — relay stays OFF",
            temp,
            TEMPERATURE_THRESHOLD,
        )

    # Show audit log
    entries = await audit.get_recent()
    logger.info("Audit log: %d entries", len(entries))
    for entry in entries:
        logger.info("  %s: %s %s", entry["timestamp"], entry["action"], entry["params"])

    await bridge.disconnect()
    await audit.close()
    logger.info("Demo complete.")


if __name__ == "__main__":
    asyncio.run(run_demo())
