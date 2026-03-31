"""MQTT bridge — connects to broker, discovers devices, stores telemetry.

All MQTT I/O is isolated in this module. Other modules call its methods
to read telemetry or send commands without touching MQTT directly.
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable
from enum import Enum
from typing import Any

import paho.mqtt.client as mqtt

from thingwire.config import GatewayConfig
from thingwire.td_loader import ThingDescription, parse_thing_description_dict

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    """MQTT connection state machine."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


class MqttBridge:
    """Manages MQTT connection, device discovery, and command routing."""

    def __init__(self, config: GatewayConfig) -> None:
        self._config = config
        self._client: mqtt.Client | None = None
        self._devices: dict[str, dict[str, Any]] = {}
        self._telemetry: dict[str, dict[str, Any]] = {}
        self._status: dict[str, str] = {}
        self._last_telemetry_time: dict[str, float] = {}  # device heartbeat from telemetry
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connected = asyncio.Event()
        self._state = ConnectionState.DISCONNECTED
        self._reconnect_count = 0
        self._shutting_down = False
        self.on_device_discovered: Callable[[str, dict[str, Any]], None] | None = None

    @property
    def prefix(self) -> str:
        """MQTT topic prefix."""
        return self._config.device_topic_prefix

    @property
    def state(self) -> ConnectionState:
        """Current connection state."""
        return self._state

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
            self._state = ConnectionState.DISCONNECTED
            return

        self._state = ConnectionState.CONNECTED
        self._reconnect_count = 0

        logger.info(
            "Connected to MQTT broker at %s:%d",
            self._config.mqtt_broker,
            self._config.mqtt_port,
        )

        # Re-subscribe on every connect (handles reconnection)
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
        """Handle broker disconnect with reconnection tracking."""
        if self._loop:
            self._loop.call_soon_threadsafe(self._connected.clear)

        if self._shutting_down:
            self._state = ConnectionState.DISCONNECTED
            logger.info("Disconnected from MQTT broker (clean shutdown)")
            return

        if rc != 0:
            self._state = ConnectionState.RECONNECTING
            self._reconnect_count += 1
            logger.warning(
                "Unexpected MQTT disconnect (rc=%d), reconnect attempt #%d",
                rc,
                self._reconnect_count,
            )
        else:
            self._state = ConnectionState.DISCONNECTED

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
            is_new = device_id not in self._devices
            self._devices[device_id] = td
            logger.info("Discovered device: %s (%s)", device_id, td.get("title", "unknown"))
            if is_new and self.on_device_discovered:
                self.on_device_discovered(device_id, td)
        except json.JSONDecodeError:
            logger.error("Invalid TD JSON from device %s", device_id)

    def _handle_telemetry(self, device_id: str, payload: bytes) -> None:
        """Store latest telemetry readings and update heartbeat timestamp."""
        try:
            data = json.loads(payload)
            self._telemetry[device_id] = data
            self._last_telemetry_time[device_id] = time.monotonic()
            logger.debug("Telemetry from %s", device_id)
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
        self._state = ConnectionState.CONNECTING
        self._shutting_down = False

        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"thingwire-gateway-{uuid.uuid4().hex[:8]}",
        )

        # Paho handles reconnection internally, but we configure sensible limits
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        try:
            self._client.connect(self._config.mqtt_broker, self._config.mqtt_port)
        except OSError as e:
            self._state = ConnectionState.DISCONNECTED
            host = self._config.mqtt_broker
            port = self._config.mqtt_port
            msg = f"Could not connect to MQTT broker at {host}:{port}: {e}"
            raise ConnectionError(msg) from e

        self._client.loop_start()

        try:
            await asyncio.wait_for(self._connected.wait(), timeout=10.0)
        except TimeoutError:
            self._state = ConnectionState.DISCONNECTED
            if self._client:
                self._client.loop_stop()
                self._client.disconnect()
                self._client = None
            msg = (
                f"Timed out connecting to MQTT broker at "
                f"{self._config.mqtt_broker}:{self._config.mqtt_port}"
            )
            raise ConnectionError(msg) from None

    async def disconnect(self) -> None:
        """Gracefully disconnect from MQTT broker."""
        self._shutting_down = True
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                logger.exception("Error during MQTT disconnect")
            finally:
                self._client = None
                self._state = ConnectionState.DISCONNECTED
            logger.info("Disconnected from MQTT broker")

    async def wait_for_devices(self, timeout: float = 30.0) -> list[str]:
        """Wait up to timeout seconds for at least one device to be discovered."""
        deadline = asyncio.get_event_loop().time() + timeout
        while not self._devices:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.5, remaining))

        if self._devices:
            logger.info("Discovered %d device(s): %s", len(self._devices), list(self._devices))
        else:
            logger.warning("No devices discovered within %.0fs", timeout)

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
        """Return latest reading dict for a specific property."""
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

    def get_last_telemetry_time(self, device_id: str) -> float | None:
        """Return monotonic timestamp of last telemetry, or None if never received."""
        return self._last_telemetry_time.get(device_id)

    async def send_command(
        self,
        device_id: str,
        target: str,
        command: str,
        value: Any,
    ) -> str:
        """Publish a command to the device and return the action_id."""
        if not self._client:
            raise RuntimeError("Not connected to MQTT broker")

        if self._state != ConnectionState.CONNECTED:
            raise RuntimeError(f"MQTT bridge is {self._state.value}, cannot send commands")

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
        result = self._client.publish(topic, payload)

        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            logger.error("Failed to publish command to %s (rc=%d)", topic, result.rc)
            raise RuntimeError(f"MQTT publish failed with rc={result.rc}")

        logger.info(
            "Sent command to %s/%s: %s=%s (action_id=%s)",
            device_id,
            target,
            command,
            value,
            action_id,
        )
        return action_id

    def is_device_online(self, device_id: str) -> bool:
        """Return True if the device last reported an online status."""
        return self._status.get(device_id) == "online"

    def get_device_status(self, device_id: str) -> str:
        """Return device status string: 'online', 'offline', or 'unknown'."""
        return self._status.get(device_id, "unknown")
