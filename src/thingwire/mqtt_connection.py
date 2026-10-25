"""MQTT connection state machine and paho connect/disconnect callbacks."""

import asyncio
import logging
from enum import Enum
from typing import Any

import paho.mqtt.client as mqtt

from thingwire.config import GatewayConfig

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    """MQTT connection state machine."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


def subscriptions(prefix: str) -> list[str]:
    """Topics the gateway subscribes to on every (re)connect."""
    return [
        f"{prefix}/+/td",
        f"{prefix}/+/telemetry",
        f"{prefix}/+/status",
        f"{prefix}/+/estop",
        f"{prefix}/estop",
    ]


class ConnectionCallbacks:
    """Mixin with the paho on_connect/on_disconnect handlers used by MqttBridge."""

    _config: GatewayConfig
    _loop: asyncio.AbstractEventLoop | None
    _connected: asyncio.Event
    _state: ConnectionState
    _reconnect_count: int
    _shutting_down: bool

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
        """Handle broker connection: subscribe to all device topics."""
        if rc != 0:
            logger.error(
                "MQTT connection failed with code %s (if the broker uses the ACL profile, check "
                "THINGWIRE_MQTT_USERNAME and THINGWIRE_MQTT_PASSWORD)",
                rc,
            )
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
        for topic in subscriptions(self.prefix):
            client.subscribe(topic)

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
