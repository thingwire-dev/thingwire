"""MQTT bridge — connects to broker, discovers devices, stores telemetry.

All MQTT I/O is isolated in this module. Other modules call its methods
to read telemetry or send commands without touching MQTT directly.
"""

import asyncio
import json
import logging
import uuid
from typing import Any

import paho.mqtt.client as mqtt

from gateway.config import GatewayConfig
from gateway.td_loader import ThingDescription, parse_thing_description_dict

logger = logging.getLogger(__name__)


class MqttBridge:
    """Manages MQTT connection, device discovery, and command routing."""

    def __init__(self, config: GatewayConfig) -> None:
        self._config = config
        self._client: mqtt.Client | None = None
        self._devices: dict[str, dict[str, Any]] = {}  # device_id → TD dict
        self._telemetry: dict[str, dict[str, Any]] = {}  # device_id → latest telemetry
        self._status: dict[str, str] = {}  # device_id → "online"/"offline"
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connected = asyncio.Event()

    @property
    def prefix(self) -> str:
        """MQTT topic prefix."""
        return self._config.device_topic_prefix

    def _on_connect(
        self,
        client: mqtt.Client,
        _userdata: Any,
        _flags: Any,
        rc: int,
        _props: Any = None,
    ) -> None:
        """Handle broker connection — subscribe to all device topics."""
        if rc != 0:
            logger.error("MQTT connection failed with code %d", rc)
            return

        logger.info(
            "Connected to MQTT broker at %s:%d",
            self._config.mqtt_broker,
            self._config.mqtt_port,
        )

        client.subscribe(f"{self.prefix}/+/td")
        client.subscribe(f"{self.prefix}/+/telemetry")
        client.subscribe(f"{self.prefix}/+/status")

        if self._loop:
            self._loop.call_soon_threadsafe(self._connected.set)

    def _on_disconnect(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        rc: int,
        _props: Any = None,
    ) -> None:
        """Handle broker disconnect."""
        if self._loop:
            self._loop.call_soon_threadsafe(self._connected.clear)
        if rc != 0:
            logger.warning("Unexpected MQTT disconnect (rc=%d), will auto-reconnect", rc)

    def _on_message(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        msg: mqtt.MQTTMessage,
    ) -> None:
        """Route incoming messages to the appropriate handler."""
        parts = msg.topic.split("/")
        if len(parts) < 3:
            return

        device_id = parts[1]
        subtopic = parts[2]

        try:
            if subtopic == "td":
                self._handle_td(device_id, msg.payload)
            elif subtopic == "telemetry":
                self._handle_telemetry(device_id, msg.payload)
            elif subtopic == "status":
                self._handle_status(device_id, msg.payload)
        except Exception:
            logger.exception("Error handling message on %s", msg.topic)

    def _handle_td(self, device_id: str, payload: bytes) -> None:
        """Store raw Thing Description dict for a device."""
        try:
            td = json.loads(payload)
            self._devices[device_id] = td
            logger.info("Discovered device: %s (%s)", device_id, td.get("title", "unknown"))
        except json.JSONDecodeError:
            logger.error("Invalid TD JSON from device %s", device_id)

    def _handle_telemetry(self, device_id: str, payload: bytes) -> None:
        """Store latest telemetry readings for a device."""
        try:
            data = json.loads(payload)
            self._telemetry[device_id] = data
            logger.debug("Telemetry from %s: %s", device_id, data)
        except json.JSONDecodeError:
            logger.error("Invalid telemetry JSON from device %s", device_id)

    def _handle_status(self, device_id: str, payload: bytes) -> None:
        """Track device online/offline status."""
        status = payload.decode("utf-8", errors="replace").strip()
        if status in ("online", "offline"):
            self._status[device_id] = status
            logger.info("Device %s is now %s", device_id, status)
        else:
            logger.warning("Unknown status payload from %s: %r", device_id, status)

    async def connect(self) -> None:
        """Connect to MQTT broker and wait until subscriptions are active."""
        self._loop = asyncio.get_running_loop()

        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"thingwire-gateway-{uuid.uuid4().hex[:8]}",
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        self._client.connect(self._config.mqtt_broker, self._config.mqtt_port)
        self._client.loop_start()

        try:
            await asyncio.wait_for(self._connected.wait(), timeout=10.0)
        except TimeoutError:
            msg = (
                f"Could not connect to MQTT broker at "
                f"{self._config.mqtt_broker}:{self._config.mqtt_port}"
            )
            raise ConnectionError(msg)

    async def disconnect(self) -> None:
        """Disconnect from MQTT broker."""
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
            logger.info("Disconnected from MQTT broker")

    async def wait_for_devices(self, timeout: float = 30.0) -> list[str]:
        """Wait up to timeout seconds for at least one device to be discovered."""
        deadline = asyncio.get_event_loop().time() + timeout
        while not self._devices:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.5, remaining))
        return list(self._devices)

    def get_devices(self) -> list[str]:
        """Return list of discovered device IDs."""
        return list(self._devices)

    def get_td(self, device_id: str) -> ThingDescription | None:
        """Return parsed ThingDescription for a device, or None if unknown."""
        raw = self._devices.get(device_id)
        if raw is None:
            return None
        try:
            return parse_thing_description_dict(raw)
        except ValueError:
            logger.error("Could not parse stored TD for device %s", device_id)
            return None

    def get_latest_reading(self, device_id: str, property_name: str) -> dict[str, Any]:
        """Return latest reading dict for a specific property.

        Returns an error dict when the device or property is not available.
        """
        telemetry = self._telemetry.get(device_id)
        if not telemetry:
            return {
                "error": f"No telemetry available for device {device_id}",
                "device_id": device_id,
                "property": property_name,
            }

        readings = telemetry.get("readings", {})
        if property_name not in readings:
            return {
                "error": f"Property '{property_name}' not found in telemetry",
                "device_id": device_id,
                "available": list(readings),
            }

        reading = readings[property_name]
        return {
            "device_id": device_id,
            "property": property_name,
            "value": reading["value"],
            "unit": reading.get("unit"),
            "timestamp": telemetry.get("timestamp"),
        }

    async def send_command(
        self,
        device_id: str,
        target: str,
        command: str,
        value: Any,
    ) -> str:
        """Publish a command to the device and return the action_id.

        Fire-and-forget: publishes to thingwire/{device_id}/command and
        returns a UUID action_id the caller can use to correlate acks.
        """
        if not self._client:
            raise RuntimeError("Not connected to MQTT broker")

        action_id = str(uuid.uuid4())
        payload = json.dumps(
            {
                "action_id": action_id,
                "target": target,
                "command": command,
                "value": value,
            }
        )

        topic = f"{self.prefix}/{device_id}/command"
        self._client.publish(topic, payload)
        logger.info("Sent command to %s/%s: %s=%s (action_id=%s)", device_id, target, command, value, action_id)
        return action_id

    def is_device_online(self, device_id: str) -> bool:
        """Return True if the device last reported an online status."""
        return self._status.get(device_id) == "online"

    def get_device_status(self, device_id: str) -> str:
        """Return device status string: 'online', 'offline', or 'unknown'."""
        return self._status.get(device_id, "unknown")
