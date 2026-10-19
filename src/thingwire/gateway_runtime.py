"""Process wiring for the gateway: safety state, e-stop adapter, sweeps, recorder, telemetry.

Kept out of `__main__` so each piece can be built and tested on its own.
"""

import asyncio
import logging
import os
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from thingwire.audit_log import AuditLog
from thingwire.config import GatewayConfig
from thingwire.gateway_actions import ActionPipeline
from thingwire.gateway_estop import ResetInbox
from thingwire.gateway_taint import TaintTracker
from thingwire.mcp_server import attach_pipeline, register_device_tools, register_meta_tools
from thingwire.mqtt_bridge import MqttBridge
from thingwire.recorder import Recorder
from thingwire.safety import SafetyLayer
from thingwire.safety_bridge import SafetyBridgeAdapter

logger = logging.getLogger(__name__)


def _ensure_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def build_safety(config: GatewayConfig) -> SafetyLayer:
    """SafetyLayer with a persistent e-stop latch and the configured resetters."""
    _ensure_dir(config.safety_state_db_path)
    safety = SafetyLayer(
        config.safety_config_path,
        state_db_path=config.safety_state_db_path,
        authorized_resetters=config.resetters(),
    )
    with suppress(OSError):  # the reset inbox lives here; only the gateway user may write it
        os.chmod(config.safety_state_db_path, 0o600)
    return safety


def build_recorder(config: GatewayConfig) -> Recorder | None:
    """MCAP recorder when THINGWIRE_RECORD_PATH is set."""
    if not config.record_path:
        return None
    _ensure_dir(config.record_path)
    logger.info("Recording session to %s", config.record_path)
    return Recorder(Path(config.record_path))


def telemetry_hook(
    safety: SafetyLayer, recorder: Recorder | None
) -> Callable[[str, dict[str, Any]], None]:
    """Telemetry with a timestamp is the deadman heartbeat (contracts.md 1.1).

    The same heartbeat is recorded so replay feeds the deadman switch the same input.
    Runs on the MQTT thread; never blocks.
    """

    def on_telemetry(device_id: str, data: dict[str, Any]) -> None:
        if recorder is not None:
            readings = data.get("readings")
            recorder.record_telemetry(device_id, readings if isinstance(readings, dict) else {})
        if data.get("timestamp"):
            safety.record_heartbeat(device_id)
            if recorder is not None:
                recorder.record_heartbeat(device_id)

    return on_telemetry


class SafetyLoop:
    """Periodic deadman sweep, human reset inbox and rate limiter pruning."""

    def __init__(self, safety: SafetyLayer, inbox: ResetInbox, interval: float) -> None:
        self._safety, self._inbox, self._interval = safety, inbox, interval

    def tick(self) -> None:
        """One pass. Each step is isolated so one failure does not skip the others."""
        for step in (self._safety.sweep_deadman, self._process_inbox):
            try:
                step()
            except Exception:
                logger.exception("Safety sweep step failed")
        self._safety.prune_rate_limiters()

    def _process_inbox(self) -> None:
        self._inbox.process(self._safety)

    async def run(self) -> None:
        """Tick forever off the event loop thread (sqlite I/O)."""
        while True:
            await asyncio.to_thread(self.tick)
            await asyncio.sleep(self._interval)


@dataclass
class Gateway:
    """Everything the gateway process owns."""

    config: GatewayConfig
    mcp: FastMCP
    bridge: MqttBridge
    safety: SafetyLayer
    audit: AuditLog
    recorder: Recorder | None
    tasks: set[asyncio.Task[None]] = field(default_factory=set)

    def register_device(self, device_id: str, raw_td: dict[str, Any]) -> list[str]:
        """Safety rules, MCAP td record and MCP tools for one device (keyed by MQTT id)."""
        td = self.bridge.get_td(device_id)
        if td is None:
            return []
        self.safety.register_device(device_id, raw_td)
        if self.recorder is not None:
            self.recorder.record_td(device_id, raw_td)
        return register_device_tools(
            self.mcp, td, self.bridge, self.safety, self.audit, device_id=device_id
        )

    def start_background(self, loop: asyncio.AbstractEventLoop) -> None:
        """Wire the e-stop topics and telemetry heartbeat, start the safety sweep."""
        adapter = SafetyBridgeAdapter(self.safety, self.bridge, self.audit, loop)
        self.bridge.on_estop = adapter.on_estop_message
        self.bridge.on_telemetry = telemetry_hook(self.safety, self.recorder)
        inbox = ResetInbox(self.config.safety_state_db_path, self.config.estop_reset_ttl_seconds)
        sweep = SafetyLoop(self.safety, inbox, self.config.safety_sweep_seconds)
        task = loop.create_task(sweep.run())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def stop(self) -> None:
        """Cancel background tasks and close the recorder."""
        for task in list(self.tasks):
            task.cancel()
        if self.recorder is not None:
            await asyncio.to_thread(self.recorder.close)


def build_gateway(
    config: GatewayConfig, mcp: FastMCP, bridge: MqttBridge, audit: AuditLog
) -> Gateway:
    """Assemble the gateway and attach one shared tool pipeline to the MCP server."""
    safety = build_safety(config)
    recorder = build_recorder(config)
    taint = TaintTracker.from_config(safety.config)
    attach_pipeline(mcp, ActionPipeline(bridge, safety, audit, recorder=recorder, taint=taint))
    register_meta_tools(mcp, bridge, audit, safety)
    return Gateway(config, mcp, bridge, safety, audit, recorder)
