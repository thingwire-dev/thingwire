"""Gateway arm: the real ActionPipeline with identity on, a scripted approver, no network."""

import logging
from pathlib import Path
from typing import Any

from inj_arms import UNKNOWN, Outcome, Registry
from inj_model import HumanMode
from inj_tds import DESK, RELAY
from inj_world import DeviceAdapter, IdSource, SimClock

from thingwire.gateway_actions import ActionPipeline
from thingwire.gateway_taint import TaintTracker
from thingwire.identity import AgentIdentity, IdentitySettings
from thingwire.identity_gate import Gatekeeper, TheAuthClient
from thingwire.safety import SafetyLayer

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent / "safety_config.yaml"
AGENT_ID = "bench-agent"
DEFAULT_SCOPES = (
    f"device:{DESK}:read",
    f"device:{DESK}:act:look_at",
    f"device:{DESK}:act:set_expression",
    f"device:{DESK}:act:nod",
    f"device:{RELAY}:read",
)
_REASON_CODES = {"missing scope": "MISSING_SCOPE", "unauthenticated": "IDENTITY_DENIED"}


class RecordingAudit:
    """In-memory audit sink with the signature the pipeline expects."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def record(
        self,
        device_id: str,
        action: str,
        params: dict[str, Any],
        result: dict[str, Any],
        confirmed: bool = False,
        *,
        agent_id: str | None = None,
        delegation_chain: list[str] | None = None,
        scopes: list[str] | None = None,
        decision: str | None = None,
        reason: str | None = None,
    ) -> int:
        self.rows.append({"device_id": device_id, "action": action, "decision": decision})
        return len(self.rows)


class ScriptedAuth(TheAuthClient):
    """Stands in for theAuth. A human either never answers or approves exactly one call."""

    def __init__(self, human: HumanMode) -> None:
        self._grants = 1 if human == "approves_once" else 0
        self._had_grant = self._grants > 0

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None, **_: Any
    ) -> tuple[int, dict[str, Any]]:
        if path == "/thingwire/approvals":
            return 201, {"approval_id": "scripted"}
        if self._grants > 0:
            self._grants -= 1
            return 200, {"code": "approval_consumed", "proceed": True}
        code = "approval_already_used" if self._had_grant else "approval_denied"
        return 200, {"code": code, "proceed": False}


def _code(result: dict[str, Any]) -> str:
    if "error" not in result and "reason" not in result:
        return str(result.get("code", "OK"))
    if result.get("code"):
        return str(result["code"])
    reason = str(result.get("reason", ""))
    if reason in _REASON_CODES:
        return _REASON_CODES[reason]
    if reason.startswith("approval"):
        return "APPROVAL_DENIED"
    return str(result.get("error", "UNKNOWN"))


class GatewayArm:
    """Every call goes through ActionPipeline; the safety layer is never bypassed."""

    name = "gateway"

    def __init__(
        self,
        registry: Registry,
        adapter: DeviceAdapter,
        clock: SimClock,
        ids: IdSource,
        scopes: tuple[str, ...],
        human: HumanMode,
    ) -> None:
        self._reg, self._dev = registry, adapter
        self.safety = SafetyLayer(str(CONFIG_PATH), clock=clock, id_factory=ids)
        for device, td in registry.tds.items():
            self.safety.register_device(device, td)
        taint = TaintTracker.from_config(self.safety.config, clock)
        self.audit = RecordingAudit()
        self._pipe = ActionPipeline(adapter, self.safety, self.audit, taint=taint)
        settings = IdentitySettings(
            issuer="http://localhost:9200", gateway_token="bench", approval_poll=0.0
        )
        self._gate = Gatekeeper(settings, ScriptedAuth(human))
        self._ident = AgentIdentity(
            AGENT_ID, f"agent:{AGENT_ID}", (f"agent:{AGENT_ID}",), frozenset(scopes), "j1"
        )

    async def invoke(self, tool: str, params: dict[str, Any], origin: str) -> Outcome:
        self._dev.origin = origin
        if tool == "emergency_stop":
            device = str(params.get("device_id", ""))
            res = await self._pipe.emergency_stop(
                device,
                str(params.get("reason", "")),
                device in self._reg.tds,
                self._ident,
                self._gate,
            )
            return Outcome("meta", res.get("status") == "estop_latched", False, _code(res))
        entry = self._reg.tools.get(tool)
        if entry is None:
            return UNKNOWN
        c = entry.compiled
        if c.tool_type == "read":
            res = await self._pipe.read(c, self._ident, self._gate)
            return Outcome("read", "error" not in res, False, _code(res), res.get("value"))
        res = await self._pipe.execute(c, entry.route, params, self._ident, self._gate)
        sent = res.get("status") == "command_sent"
        return Outcome("action", sent, sent, _code(res))

    def describe(self, device: str) -> list[str]:
        return self._reg.describe(device)

    def on_estop(self, device: str) -> None:
        self.safety.trip_estop(device or None, "benchmark e-stop", "benchmark")

    def heartbeat(self) -> None:
        for device in self._reg.tds:
            self.safety.record_heartbeat(device)
