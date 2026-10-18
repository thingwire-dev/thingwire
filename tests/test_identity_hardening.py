"""Regression tests for the PR 10 security review findings."""

import asyncio
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import jwt
import pytest
from click.testing import CliRunner
from fastmcp import Client

from tests.test_identity import AGENT, AUD, ISS, KID, claims, jwks_for
from tests.test_identity_gate import DEV, GRANTED, PENDING, TD, FakeAuth, gatekeeper, ident
from tests.test_identity_server import FakeBridge, RecordingAudit, call
from thingwire import mcp_server
from thingwire.cli import main
from thingwire.identity import (
    AgentTokenVerifier,
    IdentityError,
    IdentitySettings,
    JwksKeys,
    check_sse_host,
)
from thingwire.identity_gate import TheAuthClient
from thingwire.mcp_server import create_mcp_server, register_device_tools
from thingwire.safety import SafetyLayer
from thingwire.safety_codes import DecisionCode


@pytest.fixture
def key() -> Any:
    from cryptography.hazmat.primitives.asymmetric import ed25519

    return ed25519.Ed25519PrivateKey.generate()


def token(key: Any, **over: Any) -> str:
    return jwt.encode(claims(**over), key, algorithm="EdDSA", headers={"kid": KID, "typ": "at+jwt"})


def verifier(key: Any, **kw: Any) -> AgentTokenVerifier:
    s = IdentitySettings(issuer=ISS, audience=AUD, gateway_token="x")
    return AgentTokenVerifier(s, JwksKeys(lambda: jwks_for((KID, key)), **kw))


# 2. actor attribution follows theauth chain.Prepend (newest actor is the top-level act)
async def test_top_level_act_is_the_actor(key: Any) -> None:
    act = {"sub": "agent:BBBB", "act": {"sub": "agent:AAAA"}}
    ident_ = await verifier(key).verify(token(key, sub="USER1", act=act))
    assert ident_.agent_id == "BBBB"
    assert ident_.delegation_chain == ("USER1", "agent:BBBB", "agent:AAAA")


async def test_low_privilege_agent_cannot_borrow_identity_by_nesting(key: Any) -> None:
    """Agent B holding a token that nests privileged A is still attributed to B."""
    act = {"sub": "agent:LOWPRIV", "act": {"sub": "agent:PRIVILEGED"}}
    ident_ = await verifier(key).verify(token(key, sub="USER1", act=act))
    assert ident_.agent_id == "LOWPRIV"


# 4. revocation window is bounded by a short max token lifetime
async def test_max_token_lifetime_default_900(key: Any) -> None:
    assert IdentitySettings().max_token_lifetime == 900
    now = int(time.time())
    await verifier(key).verify(token(key, iat=now, exp=now + 900))
    with pytest.raises(IdentityError, match="lifetime"):
        await verifier(key).verify(token(key, iat=now, exp=now + 901))


# 5. sse has no identity layer
def test_sse_refused_unless_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(IdentityError):
        check_sse_host(None)
    with pytest.raises(IdentityError):
        check_sse_host("0.0.0.0")
    check_sse_host("127.0.0.1")
    res = CliRunner().invoke(main, ["serve", "--transport", "sse"])
    assert res.exit_code == 1 and "--transport http" in res.output
    monkeypatch.setenv("THINGWIRE_TRANSPORT", "sse")
    monkeypatch.setenv("THINGWIRE_SSE_HOST", "0.0.0.0")
    with pytest.raises(IdentityError):
        create_mcp_server()


# 6. a valid token must not be rejected once per JWKS ttl
async def test_ttl_expiry_reloads_instead_of_clearing(key: Any) -> None:
    calls: list[int] = []

    def fetch() -> dict[str, Any]:
        calls.append(1)
        return jwks_for((KID, key))

    v = AgentTokenVerifier(
        IdentitySettings(issuer=ISS, audience=AUD, gateway_token="x"),
        JwksKeys(fetch, ttl=0.0, min_refresh=30.0),
    )
    t = token(key)
    await v.verify(t)
    await asyncio.sleep(0.01)
    assert (await v.verify(t)).agent_id == AGENT
    assert len(calls) == 2


async def test_ttl_reload_failure_keeps_recent_keys_but_not_stale_ones(key: Any) -> None:
    doc = {"ok": True}

    def fetch() -> dict[str, Any]:
        if not doc["ok"]:
            raise OSError("down")
        return jwks_for((KID, key))

    keys = JwksKeys(fetch, ttl=100.0)
    await keys.key_for(KID)
    doc["ok"] = False
    keys._fetched_at -= 150  # past ttl, within 2 * ttl
    assert await keys.key_for(KID) is not None
    keys._fetched_at -= 500  # long past 2 * ttl
    with pytest.raises(IdentityError):
        await keys.key_for(KID)


# 7. an action named "dangerous" would collide with the device-wide scope
def test_action_named_dangerous_denied() -> None:
    g = gatekeeper(FakeAuth([GRANTED]))
    have = ident(f"device:{DEV}:act:dangerous")
    for dangerous in (True, False):
        v = g.check_scope(have, DEV, "dangerous", dangerous)
        assert not v.allowed and v.reason == "reserved action name"


async def test_tool_named_dangerous_not_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    td_dict = json.loads(json.dumps(TD))
    td_dict["actions"]["dangerous"] = dict(td_dict["actions"]["sweep"], title="x")
    from thingwire.td_loader import parse_thing_description

    mcp = create_mcp_server()
    bridge = FakeBridge()
    names = register_device_tools(
        mcp,
        parse_thing_description(json.dumps(td_dict)),
        bridge,  # type: ignore[arg-type]
        SafetyLayer(),
        RecordingAudit(),  # type: ignore[arg-type]
    )
    assert "do_dangerous" not in names and "do_sweep" in names


# 8. the identity client never follows redirects or forwards credentials
def test_client_does_not_follow_redirects_or_forward_token() -> None:
    seen: list[str | None] = []

    class Target(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            seen.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_: Any) -> None:
            pass

    target = HTTPServer(("127.0.0.1", 0), Target)

    class Redirector(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(307)
            self.send_header("Content-Length", "0")
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/steal")
            self.end_headers()

        def log_message(self, *_: Any) -> None:
            pass

    redirector = HTTPServer(("127.0.0.1", 0), Redirector)
    for srv in (target, redirector):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        client = TheAuthClient(f"http://127.0.0.1:{redirector.server_port}", "secret-token")
        status, _ = client.request("POST", "/thingwire/approvals", {})
        assert status == 307 and seen == []
    finally:
        target.shutdown()
        redirector.shutdown()


# 9. approval flooding
async def test_pending_approvals_capped_per_agent() -> None:
    s = IdentitySettings(
        gateway_token="x", approval_timeout=0.3, approval_poll=0.01, max_pending_approvals=2
    )
    from thingwire.identity_gate import Gatekeeper

    g = Gatekeeper(s, FakeAuth([PENDING]))
    runs = [asyncio.create_task(g.wait_approval(ident(), DEV, "sweep", {"n": i})) for i in range(4)]
    verdicts = await asyncio.gather(*runs)
    reasons = sorted(v.reason for v in verdicts)
    assert reasons.count("too many pending approvals") == 2
    assert reasons.count("approval timeout") == 2
    # counter is released, so a later request is evaluated again
    assert (await g.wait_approval(ident(), DEV, "sweep", {})).reason == "approval timeout"


async def test_service_429_maps_to_limit_denial() -> None:
    g = gatekeeper(FakeAuth([PENDING], opened=(429, {"code": "approval_limit"})))
    assert (await g.wait_approval(ident(), DEV, "sweep", {})).reason == "too many pending approvals"


# 1. the approval id is a credential and must not reach logs; operator token stays off the gateway
async def test_approval_id_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    client = FakeAuth([GRANTED], opened=(201, {"approval_id": "SECRETAPPROVALID123"}))
    assert (await gatekeeper(client).wait_approval(ident(), DEV, "sweep", {})).allowed
    assert "SECRETAPPROVALID123" not in caplog.text and "ref " in caplog.text


@pytest.mark.parametrize("var", ["THINGWIRE_OPERATOR_TOKEN", "THEAUTH_OPERATOR_TOKEN"])
def test_gateway_refuses_to_start_with_operator_token(
    monkeypatch: pytest.MonkeyPatch, var: str
) -> None:
    monkeypatch.setenv(var, "o" * 30)
    with pytest.raises(IdentityError, match="operator token"):
        IdentitySettings(issuer=ISS, audience=AUD, gateway_token="g" * 30).validate_complete()


def test_approve_cli_needs_operator_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("THINGWIRE_OPERATOR_TOKEN", raising=False)
    monkeypatch.setenv("THINGWIRE_AUTH_GATEWAY_TOKEN", "g" * 30)  # gateway token is not enough
    res = CliRunner().invoke(main, ["approve", "abc"])
    assert res.exit_code != 0 and "operator-token" in res.output.lower()


# 3. state can change while waiting for the human
def build(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, FakeBridge, RecordingAudit, SafetyLayer]:
    monkeypatch.setenv("THINGWIRE_TRANSPORT", "http")
    monkeypatch.setenv("THINGWIRE_AUTH_ISSUER", ISS)
    monkeypatch.setenv("THINGWIRE_AUTH_GATEWAY_TOKEN", "x")
    mcp = create_mcp_server()
    bridge, audit, safety = FakeBridge(), RecordingAudit(), SafetyLayer()
    safety.register_device(str(TD["id"]), TD)
    register_device_tools(mcp, bridge.get_td(DEV), bridge, safety, audit)  # type: ignore[arg-type]
    return mcp, bridge, audit, safety


class Mutating(FakeAuth):
    """Approves after running a side effect, like the world changing during the wait."""

    def __init__(self, effect: Any) -> None:
        super().__init__([GRANTED])
        self.effect = effect

    def request(self, method: str, path: str, body: Any = None, **kw: Any) -> Any:
        if path.endswith("/spend"):
            self.effect()
        return super().request(method, path, body, **kw)


@pytest.mark.parametrize("what", ["estop", "offline", "deadman"])
async def test_state_change_during_approval_wait_denies(
    monkeypatch: pytest.MonkeyPatch, what: str
) -> None:
    mcp, bridge, audit, safety = build(monkeypatch)
    td_id = str(TD["id"])

    def effect() -> None:
        if what == "estop":
            safety.guards.trip(td_id, "operator pressed stop", "test", DecisionCode.ESTOP_LATCHED)
        elif what == "offline":
            bridge.is_device_online = lambda _d: False  # type: ignore[method-assign]
        else:
            safety._last_heartbeat[td_id] = time.monotonic() - 10_000

    mcp.gate = gatekeeper(Mutating(effect))
    out = await call(mcp, "do_sweep", {}, ident(f"device:{DEV}:act:sweep"), monkeypatch)
    assert out["error"] == "DENIED"
    assert "recheck" in out["reason"] or "offline" in out["reason"]
    assert not bridge.sent
    assert audit.rows[-1]["decision"] == "deny"


async def test_estop_before_wait_denies_without_opening_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mcp, bridge, _, safety = build(monkeypatch)
    safety.guards.trip(str(TD["id"]), "stop", "test", DecisionCode.ESTOP_LATCHED)
    fake = FakeAuth([GRANTED])
    mcp.gate = gatekeeper(fake)
    out = await call(mcp, "do_sweep", {}, ident(f"device:{DEV}:act:sweep"), monkeypatch)
    assert out["error"] == "ESTOP_LATCHED" and not fake.calls and not bridge.sent


# E-stop reset must never be an agent-facing tool
async def test_no_estop_or_reset_tool_exposed(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp, *_ = build(monkeypatch)
    mcp_server.register_meta_tools(mcp, FakeBridge(), RecordingAudit())  # type: ignore[arg-type]
    async with Client(mcp) as c:
        names = [t.name for t in await c.list_tools()]
    assert not [n for n in names if "reset" in n or "estop" in n or "latch" in n]
