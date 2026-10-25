"""MQTT bridge: connects to broker, routes device messages, sends commands.

All MQTT I/O is isolated here. Device state lives in `mqtt_devices.DeviceStore`
and the connect/disconnect state machine in `mqtt_connection`; other modules
call the bridge to read telemetry or send commands without touching MQTT.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from thingwire.config import GatewayConfig
from thingwire.mqtt_connection import ConnectionCallbacks, ConnectionState
from thingwire.mqtt_devices import DeviceStore

__all__ = ["ConnectionState", "MqttBridge"]

logger = logging.getLogger(__name__)


class MqttBridge(DeviceStore, ConnectionCallbacks):
    """Manages MQTT connection, device discovery, and command routing."""

    def __init__(self, config: GatewayConfig) -> None:
        DeviceStore.__init__(self)
        self._config = config
        self._client: mqtt.Client | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connected = asyncio.Event()
        self._state = ConnectionState.DISCONNECTED
        self._reconnect_count = 0
        self._shutting_down = False
        # (device_id or None for global, payload, retained)
        self.on_estop: Callable[[str | None, bytes, bool], None] | None = None

    def _on_message(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        msg: mqtt.MQTTMessage,
    ) -> None:
        """Route incoming messages to the appropriate handler."""
        parts = msg.topic.split("/")
        if len(parts) == 2 and parts[1] == "estop":
            self._route_estop(None, msg)
            return
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
            elif subtopic == "estop":
                self._route_estop(device_id, msg)
        except Exception:
            logger.exception("Error handling message on %s", msg.topic)

    def _route_estop(self, device_id: str | None, msg: mqtt.MQTTMessage) -> None:
        """Hand an e-stop message to the safety adapter. Never raises."""
        try:
            if self.on_estop:
                self.on_estop(device_id, bytes(msg.payload), bool(msg.retain))
        except Exception:
            logger.exception("Error handling e-stop on %s", msg.topic)

    def publish_nowait(self, topic: str, payload: str, retain: bool = False, qos: int = 1) -> bool:
        """Queue a publish on paho's network thread (does not wait for the broker)."""
        if not self._client:
            return False
        return bool(self._client.publish(topic, payload, qos=qos, retain=retain).rc == 0)

    async def connect(self) -> None:
        """Connect to MQTT broker and wait until subscriptions are active."""
        self._loop = asyncio.get_running_loop()
        self._state = ConnectionState.CONNECTING
        self._shutting_down = False

        self._client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=f"thingwire-gateway-{uuid.uuid4().hex[:8]}",
        )

        # Paho handles reconnection internally, but we configure sensible limits
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)

        if self._config.mqtt_username:
            password = self._config.mqtt_password.get_secret_value() or None
            self._client.username_pw_set(self._config.mqtt_username, password)
            logger.info("MQTT login as %s", self._config.mqtt_username)

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
