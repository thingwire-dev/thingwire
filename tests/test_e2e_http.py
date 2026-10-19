"""End to end over real HTTP: fastmcp client, signed agent tokens, identity-enabled gateway.

Server: uvicorn on a loopback port, `create_identity_server` with real JWT verification
(JWKS from a test key), theAuth approvals scripted by FakeAuth, the virtual desk bot behind
a fake bridge, a real SQLite audit log, a persistent e-stop latch and an MCAP recorder.
"""

import asyncio
import socket
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jwt
import pytest
import uvicorn
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastmcp import Client

import thingwire.identity_gate as gate_mod
from tests.test_e2e_wiring import DEV, DeskBridge
from tests.test_identity import AGENT, AUD, ISS, KID, claims, jwks_for
from tests.test_identity_gate import FakeAuth, gatekeeper
from thingwire.audit_log import AuditLog
from thingwire.gateway_actions import ActionPipeline
from thingwire.gateway_taint import TaintTracker
from thingwire.identity import IdentitySettings
from thingwire.identity_gate import create_identity_server
from thingwire.mcp_server import attach_pipeline, register_device_tools, register_meta_tools
from thingwire.recorder import Recorder, read_recording
from thingwire.safety import SafetyLayer

DENIED = (200, {"code": "approval_denied"})
ALL = [f"device:{DEV}:read", f"device:{DEV}:act:look_at", f"device:{DEV}:act:sweep", "audit:read"]


@dataclass
class Served:
    url: str
    key: ed25519.Ed25519PrivateKey
    bridge: DeskBridge
    audit: AuditLog
    safety: SafetyLayer
    recorder: Recorder
    record_path: Path

    def token(self, *scopes: str) -> str:
        return jwt.encode(
            claims(scope=" ".join(scopes)),
            self.key,
            algorithm="EdDSA",
            headers={"kid": KID, "typ": "at+jwt"},
        )

    async def call(self, tool: str, args: dict[str, Any], *scopes: str) -> dict[str, Any]:
        async with Client(self.url, auth=self.token(*scopes), timeout=10) as c:
            res = await c.call_tool(tool, args, raise_on_error=False)
        return dict(res.structured_content or {})


@pytest.fixture
async def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Served]:
    key = ed25519.Ed25519PrivateKey.generate()
    monkeypatch.setattr(gate_mod, "fetch_jwks_http", lambda _url: jwks_for((KID, key)))
    mcp = create_identity_server(
        "e2e", IdentitySettings(issuer=ISS, audience=AUD, gateway_token="x")
    )
    mcp.gate = gatekeeper(FakeAuth([DENIED]), timeout=0.5)
    bridge = DeskBridge()
    audit = AuditLog(str(tmp_path / "audit.db"))
    await audit.initialize()
    safety = SafetyLayer(state_db_path=str(tmp_path / "state.db"))
    safety.register_device(DEV, bridge.td)
    recorder = Recorder(tmp_path / "run.mcap")
    pipeline = ActionPipeline(
        bridge,
        safety,
        audit,
        recorder=recorder,
        taint=TaintTracker.from_config(safety.config),
    )
    attach_pipeline(mcp, pipeline)
    register_device_tools(mcp, bridge.get_td(DEV), bridge, safety, audit, device_id=DEV)  # type: ignore[arg-type]
    register_meta_tools(mcp, bridge, audit, safety)  # type: ignore[arg-type]

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(mcp.http_app(path="/mcp"), log_level="warning", lifespan="on")
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    for _ in range(500):
        if server.started:
            break
        await asyncio.sleep(0.01)
    assert server.started, "uvicorn did not start"
    try:
        yield Served(
            f"http://127.0.0.1:{port}/mcp",
            key,
            bridge,
            audit,
            safety,
            recorder,
            tmp_path / "run.mcap",
        )
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)
        recorder.close()
        await audit.close()
        sock.close()


async def test_full_gateway_over_http(served: Served) -> None:
    s = served
    # 1. allowed: the bot really turns
    ok = await s.call("do_look_at", {"pan": 20, "tilt": 5}, *ALL)
    assert ok["status"] == "command_sent" and ok["target"] == "look_at", ok
    assert s.bridge.acks[-1]["status"] == "ok" and s.bridge.bot._target == (20.0, 5.0)

    # 2. scope denial: a token without act:look_at
    scoped = await s.call("do_look_at", {"pan": 1, "tilt": 0}, f"device:{DEV}:read")
    assert scoped["error"] == "DENIED" and scoped["reason"] == "missing scope"

    # 3. out of range: denied by the safety layer, nothing sent
    sent = len(s.bridge.sent)
    far = await s.call("do_look_at", {"pan": 500, "tilt": 0}, *ALL)
    assert far["error"] == "OUT_OF_RANGE" and len(s.bridge.sent) == sent

    # 4. dangerous without approval: theAuth refuses, nothing sent
    sweep = await s.call("do_sweep", {"speed_deg_s": 30}, *ALL)
    assert sweep["error"] == "DENIED" and sweep["reason"] == "approval denied"
    assert len(s.bridge.sent) == sent

    # 5. agent trips the e-stop; every later motion is refused
    stop = await s.call("emergency_stop", {"device_id": DEV, "reason": "person close"}, *ALL)
    assert stop["status"] == "estop_latched"
    after = await s.call("do_look_at", {"pan": 21, "tilt": 5}, *ALL)
    assert after["error"] == "ESTOP_LATCHED" and len(s.bridge.sent) == sent
    assert SafetyLayer(state_db_path=str(s.record_path.parent / "state.db")).estop_latched(DEV)

    # 6. audit rows, with identity, for every allow and deny
    rows = await s.audit.get_recent(limit=50)
    got = [(r["action"], r["decision"], r["reason"]) for r in reversed(rows)]
    assert got == [
        ("do_look_at", "allow", "OK"),
        ("do_look_at", "deny", "missing scope"),
        ("do_look_at", "deny", "OUT_OF_RANGE"),
        ("do_sweep", "deny", "approval denied"),
        ("emergency_stop", "allow", "ESTOP_LATCHED"),
        ("do_look_at", "deny", "ESTOP_LATCHED"),
    ]
    assert {r["agent_id"] for r in rows} == {AGENT}
    via_tool = await s.call("get_audit_log", {"limit": 50}, *ALL)
    assert via_tool["count"] >= 6

    # 7. MCAP: one decision per tool call
    s.recorder.close()
    events = read_recording(s.record_path)
    codes = [e.data["code"] for e in events if e.channel == "decision"]
    assert codes == ["OK", "MISSING_SCOPE", "OUT_OF_RANGE", "APPROVAL_DENIED", "ESTOP_LATCHED"]


def _post(url: str, headers: dict[str, str]) -> int:
    body = b'{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}'
    hdrs = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    req = urllib.request.Request(url, data=body, headers=hdrs | headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


async def test_missing_or_forged_token_is_401(served: Served) -> None:
    forged = jwt.encode(
        claims(),
        ed25519.Ed25519PrivateKey.generate(),
        algorithm="EdDSA",
        headers={"kid": KID, "typ": "at+jwt"},
    )
    assert await asyncio.to_thread(_post, served.url, {}) == 401
    assert await asyncio.to_thread(_post, served.url, {"Authorization": f"Bearer {forged}"}) == 401
