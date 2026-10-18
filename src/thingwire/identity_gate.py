"""Authorization gate: scope checks, dangerous-action approvals, audit identity, HTTP server.

Split from identity.py to keep each file under 300 lines. Every failure path denies.
"""

import asyncio
import base64
import hashlib
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from fastmcp import FastMCP

from thingwire.identity import (
    RESERVED_ACTION,
    AgentIdentity,
    AgentTokenVerifier,
    IdentityError,
    IdentitySettings,
    JwksKeys,
    device_key,
    fetch_jwks_http,
    no_redirect_opener,
    scope_act,
    scope_dangerous,
    scope_read,
    valid_name,
)

logger = logging.getLogger(__name__)

CODE_CONSUMED = "approval_consumed"
CODE_PENDING = "approval_pending"
_DENY_REASONS = {
    "approval_denied": "approval denied",
    "approval_expired": "approval expired",
    "approval_already_used": "approval already used",
    "approval_arguments_mismatch": "approval does not match arguments",
    "approval_mismatch": "approval does not match call",
    "approval_not_found": "approval not found",
}


class TheAuthClient:
    """Minimal blocking client for the ThingWire theauth service (run it with to_thread)."""

    def __init__(self, base_url: str, admin_token: str, timeout: float = 10.0) -> None:
        self._base, self._token, self._timeout = base_url.rstrip("/"), admin_token, timeout

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        form: dict[str, str] | None = None,
        basic: str | None = None,
        admin: bool = True,
    ) -> tuple[int, dict[str, Any]]:
        """Send one request. Returns (status, json body). Network failures raise IdentityError."""
        data, headers = None, {}
        if body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        if form is not None:
            data, headers["Content-Type"] = (
                urlencode(form).encode(),
                "application/x-www-form-urlencoded",
            )
        if admin:
            headers["Authorization"] = f"Bearer {self._token}"
        elif basic:
            headers["Authorization"] = "Basic " + base64.b64encode(basic.encode()).decode()
        req = urllib.request.Request(self._base + path, data=data, headers=headers, method=method)
        try:
            with no_redirect_opener().open(req, timeout=self._timeout) as resp:
                return resp.status, json.loads(resp.read(1 << 20) or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read(1 << 20) or b"{}")
            except (OSError, ValueError):
                return exc.code, {}
        except (OSError, ValueError) as exc:
            raise IdentityError(f"identity service unreachable: {type(exc).__name__}") from exc

    def call_body(
        self, ident: AgentIdentity, device: str, action: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Body that binds an approval to agent, device, action and the exact arguments."""
        return {
            "agent_id": ident.agent_id,
            "device_id": device,
            "action": action,
            "arguments": params,
        }


@dataclass(frozen=True)
class Verdict:
    """Outcome of a gate check. `decision` and `reason` go to the audit log."""

    allowed: bool
    decision: str
    reason: str = ""
    required_scope: str = ""

    def error(self) -> dict[str, Any]:
        """Tool result for a denial."""
        out: dict[str, Any] = {"error": "DENIED", "reason": self.reason, "decision": self.decision}
        if self.required_scope:
            out["required_scope"] = self.required_scope
        return out


ALLOW = Verdict(True, "allow")


def deny(reason: str, required_scope: str = "") -> Verdict:
    """Build a denial."""
    return Verdict(False, "deny", reason, required_scope)


class Gatekeeper:
    """Scope mapping and approvals for one gateway."""

    def __init__(self, settings: IdentitySettings, client: TheAuthClient) -> None:
        self._s, self._client = settings, client
        self._inflight: dict[str, int] = {}

    def check_read(self, ident: AgentIdentity | None, device_id: str) -> Verdict:
        """Reading a device needs device:<id>:read."""
        device_id = device_key(device_id)
        if ident is None:
            return deny("unauthenticated")
        if not valid_name(device_id):
            return deny("invalid device name")
        need = scope_read(device_id)
        return ALLOW if need in ident.scopes else deny("missing scope", need)

    def check_scope(
        self, ident: AgentIdentity | None, device_id: str, action: str, dangerous: bool
    ) -> Verdict:
        """Acting needs act:<action>; dangerous actions also accept device-wide act:dangerous."""
        device_id = device_key(device_id)
        if ident is None:
            return deny("unauthenticated")
        if not (valid_name(device_id) and valid_name(action)):
            return deny("invalid device or action name")
        if action == RESERVED_ACTION:
            return deny("reserved action name")
        need = scope_act(device_id, action)
        if need in ident.scopes or (dangerous and scope_dangerous(device_id) in ident.scopes):
            return ALLOW
        return deny("missing scope", need)

    def check_named_scope(self, ident: AgentIdentity | None, scope: str) -> Verdict:
        """Require one literal scope (used for audit:read)."""
        if ident is None:
            return deny("unauthenticated")
        return ALLOW if scope in ident.scopes else deny("missing scope", scope)

    async def wait_approval(
        self,
        ident: AgentIdentity,
        device_id: str,
        action: str,
        params: dict[str, Any],
    ) -> Verdict:
        """Open an approval and wait for a human. Timeout, error or refusal all deny."""
        n = self._inflight.get(ident.agent_id, 0)
        if n >= self._s.max_pending_approvals:
            return deny("too many pending approvals")
        self._inflight[ident.agent_id] = n + 1
        try:
            return await self._wait(ident, device_id, action, params)
        finally:
            self._inflight[ident.agent_id] -= 1

    async def _wait(
        self, ident: AgentIdentity, device_id: str, action: str, params: dict[str, Any]
    ) -> Verdict:
        body = self._client.call_body(ident, device_key(device_id), action, params)
        try:
            status, opened = await asyncio.to_thread(
                self._client.request, "POST", "/thingwire/approvals", body
            )
            approval_id = opened.get("approval_id")
            if status == 429:
                return deny("too many pending approvals")
            if status != 201 or not isinstance(approval_id, str):
                return deny("approval service error")
            # The id is a credential for approving; only a short non-reusable ref is logged.
            ref = hashlib.sha256(approval_id.encode()).hexdigest()[:8]
            logger.warning(
                "Approval pending: %s on %s (agent %s, ref %s). Run: thingwire approve --list",
                action,
                device_id,
                ident.agent_id,
                ref,
            )
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._s.approval_timeout
            while True:
                status, res = await asyncio.to_thread(
                    self._client.request, "POST", f"/thingwire/approvals/{approval_id}/spend", body
                )
                code = res.get("code", "")
                if status == 200 and code == CODE_CONSUMED and res.get("proceed") is True:
                    return Verdict(True, "approved", "approved by operator")
                if status != 200 or code != CODE_PENDING:
                    return Verdict(False, "deny", _DENY_REASONS.get(code, "approval service error"))
                if loop.time() + self._s.approval_poll >= deadline:
                    return Verdict(False, "deny", "approval timeout")
                await asyncio.sleep(self._s.approval_poll)
        except IdentityError as exc:
            return deny(exc.reason)


async def record_audit(
    audit: Any,
    *,
    ident: AgentIdentity | None,
    verdict: Verdict | None,
    **fields: Any,
) -> None:
    """Write an audit row with agent_id, delegation_chain, scopes, decision and reason."""
    if ident is None and verdict is None:
        await audit.record(**fields)
        return
    extra: dict[str, Any] = {
        "agent_id": ident.agent_id if ident else None,
        "delegation_chain": list(ident.delegation_chain) if ident else [],
        "scopes": sorted(ident.scopes) if ident else [],
        "decision": verdict.decision if verdict else None,
        "reason": verdict.reason if verdict else None,
    }
    await audit.record(**fields, **extra)


def recheck_safety(safety: Any, bridge: Any, device_id: str, tool_name: str) -> Verdict | None:
    """Re-run the non-mutating safety checks after an approval wait (state may have changed).

    Returns a denial, or None when still safe. E-stop latch, permission, deadman and online
    state are checked; rate limits were already consumed and are not charged twice.
    """
    try:
        safety.update_heartbeat_from_telemetry(device_id, bridge.get_last_telemetry_time(device_id))
        safety.guards.check_estop(device_id, tool_name)
        safety.check_permission(device_id, tool_name)
        safety.check_deadman_switch(device_id)
        if not bridge.is_device_online(device_id):
            return Verdict(False, "deny", "device offline after approval")
    except Exception as exc:  # any failure, including SafetyError, denies
        return Verdict(
            False, "deny", f"safety recheck failed: {getattr(exc, 'code', type(exc).__name__)}"
        )
    return None


class IdentityFastMCP(FastMCP):
    """FastMCP that carries a Gatekeeper and only ever serves authenticated streamable HTTP."""

    gate: Gatekeeper | None = None
    identity_settings: IdentitySettings | None = None

    async def run_async(self, *args: Any, **kwargs: Any) -> None:
        """Ignore the requested transport: identity on means authenticated HTTP, never stdio."""
        s = self.identity_settings
        if s is None:
            raise IdentityError("identity settings missing")
        await self.run_http_async(
            transport="streamable-http", host=s.host, port=s.port, path=s.path
        )


def create_identity_server(name: str, settings: IdentitySettings) -> IdentityFastMCP:
    """Build the authenticated server. Raises IdentityError when configuration is incomplete."""
    settings.validate_complete()
    jwks_url = settings.jwks_url or settings.issuer.rstrip("/") + "/oauth/jwks"
    keys = JwksKeys(lambda: fetch_jwks_http(jwks_url))
    mcp = IdentityFastMCP(name, auth=AgentTokenVerifier(settings, keys))
    mcp.identity_settings = settings
    mcp.gate = Gatekeeper(
        settings, TheAuthClient(settings.admin_url or settings.issuer, settings.gateway_token)
    )
    return mcp
