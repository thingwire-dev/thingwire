"""Gateway tools with identity on: scope denials, approvals, audit rows, HTTP 401s."""

import asyncio
import json
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastmcp import Client, FastMCP

import thingwire.identity_gate as gate_mod
import thingwire.mcp_server as mcp_server
from tests.test_identity import AGENT, AUD, ISS, KID, claims, jwks_for
from tests.test_identity_gate import DEV, GRANTED, PENDING, TD, FakeAuth, gatekeeper, ident
from thingwire.identity import AgentIdentity, IdentityError, IdentitySettings
from thingwire.identity_gate import create_identity_server
from thingwire.mcp_server import (
    create_mcp_server,
    register_device_tools,
    register_meta_tools,
)
from thingwire.safety import SafetyLayer
from thingwire.td_loader import parse_thing_description


class RecordingAudit:
    """Audit stand-in accepting the identity keyword arguments the audit log will gain."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def record(
        self,
        device_id: str,
        action: str,
        params: dict[str, Any],
        result: dict[str, Any],
        confirmed: bool = False,
        agent_id: str | None = None,
        delegation_chain: list[str] | None = None,
        scopes: list[str] | None = None,
        decision: str | None = None,
        reason: str | None = None,
    ) -> int:
        self.rows.append(locals() | {})
        return len(self.rows)


class FakeBridge:
    def is_device_online(self, _d: str) -> bool:
        return True

    def get_latest_reading(self, _d: str, name: str) -> dict[str, Any]:
        return {"value": 1.0, "name": name}

    def get_last_telemetry_time(self, _d: str) -> float | None:
        return None

    def get_devices(self) -> list[str]:
        return [DEV]

    def get_td(self, _d: str) -> Any:
        return parse_thing_description(json.dumps(TD))

    def get_device_status(self, _d: str) -> str:
        return "online"

    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def send_command(self, **kw: Any) -> str:
        self.sent.append(kw)
        return "a1"


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> Any:
    """An identity-enabled server with the desk bot tools, a fake bridge and a recording audit."""
    monkeypatch.setenv("THINGWIRE_TRANSPORT", "http")
    monkeypatch.setenv("THINGWIRE_AUTH_ISSUER", ISS)
    monkeypatch.setenv("THINGWIRE_AUTH_GATEWAY_TOKEN", "x")
    mcp = create_mcp_server()
    bridge, audit, safety = FakeBridge(), RecordingAudit(), SafetyLayer()
    safety.register_device(str(TD["id"]), TD)  # safety is keyed by td.id
    register_device_tools(mcp, bridge.get_td(DEV), bridge, safety, audit)  # type: ignore[arg-type]
    register_meta_tools(mcp, bridge, audit)  # type: ignore[arg-type]
    mcp.gate = gatekeeper(FakeAuth([GRANTED]))  # type: ignore[attr-defined]
    return mcp, bridge, audit


async def call(
    mcp: FastMCP,
    tool: str,
    args: dict[str, Any],
    who: AgentIdentity | None,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    monkeypatch.setattr(mcp_server, "current_identity", lambda: who)
    async with Client(mcp) as c:
        res = await c.call_tool(tool, args, raise_on_error=False)
    return dict(res.structured_content or {})


class TestGatewayTools:
    async def test_no_token_no_action(self, served: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        mcp, bridge, _ = served
        out = await call(mcp, "do_look_at", {"x": 1}, None, monkeypatch)
        assert out["reason"] == "unauthenticated" and not bridge.sent

    async def test_without_act_sweep_missing_scope(
        self, served: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mcp, bridge, audit = served
        out = await call(
            mcp,
            "do_sweep",
            {},
            ident(f"device:{DEV}:read", f"device:{DEV}:act:look_at"),
            monkeypatch,
        )
        assert out["reason"] == "missing scope" and not bridge.sent
        row = audit.rows[-1]
        assert (
            row["agent_id"] == AGENT
            and row["decision"] == "deny"
            and row["reason"] == "missing scope"
        )
        assert row["scopes"] == sorted([f"device:{DEV}:read", f"device:{DEV}:act:look_at"])
        assert row["delegation_chain"] == [f"agent:{AGENT}"]

    async def test_safe_action_allowed_and_audited(
        self, served: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mcp, bridge, audit = served
        out = await call(
            mcp, "do_look_at", {"x": 2}, ident(f"device:{DEV}:act:look_at"), monkeypatch
        )
        assert out["status"] == "command_sent" and len(bridge.sent) == 1
        assert audit.rows[-1]["decision"] == "allow" and audit.rows[-1]["agent_id"] == AGENT

    async def test_sweep_with_scope_waits_for_approval(
        self, served: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mcp, bridge, audit = served
        mcp.gate = gatekeeper(FakeAuth([PENDING, PENDING, GRANTED]))
        out = await call(mcp, "do_sweep", {}, ident(f"device:{DEV}:act:sweep"), monkeypatch)
        assert out["status"] == "command_sent" and len(bridge.sent) == 1
        assert audit.rows[-1]["decision"] == "approved"

    async def test_sweep_never_approved_is_denied(
        self, served: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mcp, bridge, audit = served
        mcp.gate = gatekeeper(FakeAuth([PENDING]), timeout=0.05)
        out = await call(mcp, "do_sweep", {}, ident(f"device:{DEV}:act:sweep"), monkeypatch)
        assert out["reason"] == "approval timeout" and not bridge.sent
        assert audit.rows[-1]["decision"] == "deny"

    async def test_reads_and_meta_tools_need_scopes(
        self, served: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mcp, _, _ = served
        assert (await call(mcp, "read_pan", {}, ident(), monkeypatch))["reason"] == "missing scope"
        assert (await call(mcp, "read_pan", {}, ident(f"device:{DEV}:read"), monkeypatch))[
            "value"
        ] == 1.0
        assert (await call(mcp, "list_devices", {}, ident(), monkeypatch))["count"] == 0
        assert (await call(mcp, "list_devices", {}, ident(f"device:{DEV}:read"), monkeypatch))[
            "count"
        ] == 1
        assert (await call(mcp, "get_device_status", {"device_id": DEV}, ident(), monkeypatch))[
            "error"
        ] == "DENIED"
        assert (await call(mcp, "get_audit_log", {}, ident(f"device:{DEV}:read"), monkeypatch))[
            "reason"
        ] == "missing scope"


class TestStdioDevMode:
    async def test_stdio_unchanged_without_identity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("THINGWIRE_TRANSPORT", raising=False)
        mcp = create_mcp_server()
        bridge, audit, safety = FakeBridge(), RecordingAudit(), SafetyLayer()
        safety.register_device(str(TD["id"]), TD)  # safety is keyed by td.id
        register_device_tools(mcp, bridge.get_td(DEV), bridge, safety, audit)  # type: ignore[arg-type]
        assert getattr(mcp, "gate", None) is None
        out = await call(mcp, "do_look_at", {"x": 1}, None, monkeypatch)
        assert (out["status"] == "command_sent" and "agent_id" not in audit.rows[-1]) or audit.rows[
            -1
        ]["agent_id"] is None

    def test_http_without_identity_config_refuses_to_start(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("THINGWIRE_TRANSPORT", "http")
        for var in ("ISSUER", "GATEWAY_TOKEN"):
            monkeypatch.delenv(f"THINGWIRE_AUTH_{var}", raising=False)
        with pytest.raises(IdentityError):
            create_mcp_server()


async def asgi_post(app: Any, headers: dict[str, str]) -> int:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
    status: list[int] = []
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            await asyncio.sleep(3600)
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(msg: dict[str, Any]) -> None:
        if msg["type"] == "http.response.start":
            status.append(msg["status"])

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "scheme": "http",
        "server": ("localhost", 8000),
        "client": ("127.0.0.1", 1),
        "root_path": "",
        "headers": [
            (b"host", b"localhost:8000"),
            (b"content-type", b"application/json"),
            (b"accept", b"application/json, text/event-stream"),
            *[(k.lower().encode(), v.encode()) for k, v in headers.items()],
        ],
    }
    await asyncio.wait_for(app(scope, receive, send), timeout=5)
    return status[0]


class TestHttpTransport401:
    @pytest.fixture
    def app(self, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, ed25519.Ed25519PrivateKey]:
        key = ed25519.Ed25519PrivateKey.generate()
        monkeypatch.setattr(gate_mod, "fetch_jwks_http", lambda _url: jwks_for((KID, key)))
        mcp = create_identity_server(
            "t", IdentitySettings(issuer=ISS, audience=AUD, gateway_token="x")
        )
        return mcp.http_app(path="/mcp"), key

    def tok(self, key: ed25519.Ed25519PrivateKey, **over: Any) -> str:
        return jwt.encode(
            claims(**over), key, algorithm="EdDSA", headers={"kid": KID, "typ": "at+jwt"}
        )

    async def test_rejections_are_401(self, app: tuple[Any, ed25519.Ed25519PrivateKey]) -> None:
        asgi, key = app
        now = int(time.time())
        bad = {
            "none": {},
            "garbage": {"Authorization": "Bearer abc.def.ghi"},
            "empty": {"Authorization": "Bearer "},
            "basic": {"Authorization": "Basic Zm9vOmJhcg=="},
            "expired": {"Authorization": "Bearer " + self.tok(key, exp=now - 600, iat=now - 1500)},
            "audience": {"Authorization": "Bearer " + self.tok(key, aud="http://evil/mcp")},
            "issuer": {"Authorization": "Bearer " + self.tok(key, iss="http://evil")},
            "forged": {
                "Authorization": "Bearer "
                + jwt.encode(
                    claims(),
                    ed25519.Ed25519PrivateKey.generate(),
                    algorithm="EdDSA",
                    headers={"kid": KID, "typ": "at+jwt"},
                )
            },
        }
        for name, hdrs in bad.items():
            assert await asgi_post(asgi, hdrs) == 401, name

    async def test_valid_token_passes_auth_layer(
        self, app: tuple[Any, ed25519.Ed25519PrivateKey]
    ) -> None:
        asgi, key = app
        async with asgi.router.lifespan_context(asgi):
            status = await asgi_post(asgi, {"Authorization": "Bearer " + self.tok(key)})
        assert status not in (401, 403)
