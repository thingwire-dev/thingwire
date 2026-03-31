#!/usr/bin/env python3
"""Demo: Motion detection alerting via ThingWire MCP.

Monitors the PIR motion sensor and prints alerts when motion is detected.
Demonstrates event-driven agent responses.

Usage:
    # Start mosquitto + virtual device + gateway first, then:
    python3.11 examples/demo-motion-alert/agent-script.py
"""

import asyncio
import logging
import sys
from datetime import datetime

sys.path.insert(0, "gateway")

from gateway.config import GatewayConfig
from gateway.mqtt_bridge import MqttBridge

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("demo")

CHECK_INTERVAL = 10
ALERT_HOURS = (22, 6)  # 10 PM to 6 AM considered "unusual"


def is_unusual_hour() -> bool:
    """Check if current hour is in the 'unusual' range."""
    hour = datetime.now().hour
    start, end = ALERT_HOURS
    if start > end:
        return hour >= start or hour < end
    return start <= hour < end


async def run_demo() -> None:
    """Run the motion alert demo."""
    config = GatewayConfig()
    bridge = MqttBridge(config)

    logger.info("Connecting to MQTT broker...")
    await bridge.connect()

    logger.info("Waiting for device discovery...")
    devices = await bridge.wait_for_devices(timeout=15.0)
    if not devices:
        logger.error("No devices found. Is the virtual device running?")
        return

    device_id = devices[0]
    logger.info("Monitoring motion on device: %s", device_id)
    logger.info("Press Ctrl+C to stop.")

    last_motion = False
    try:
        while True:
            await asyncio.sleep(CHECK_INTERVAL)

            reading = bridge.get_latest_reading(device_id, "motion1")
            if "error" in reading:
                logger.warning("Could not read motion: %s", reading["error"])
                continue

            motion = reading["value"]
            if motion and not last_motion:
                if is_unusual_hour():
                    logger.warning(
                        "ALERT: Motion detected at unusual hour (%s)!",
                        datetime.now().strftime("%H:%M"),
                    )
                else:
                    logger.info("Motion detected at %s", datetime.now().strftime("%H:%M"))
            elif not motion and last_motion:
                logger.info("Motion cleared.")

            last_motion = motion

    except KeyboardInterrupt:
        logger.info("Stopping motion monitor.")
    finally:
        await bridge.disconnect()


if __name__ == "__main__":
    asyncio.run(run_demo())
