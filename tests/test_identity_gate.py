"""Scope mapping, approval wait and settings. Nothing external runs."""

from typing import Any

import pytest

import thingwire.identity_gate as gate_mod
from tests.test_identity import AGENT, AUD, ISS
from thingwire.audit_log import AuditLog
from thingwire.identity import (
    AgentIdentity,
    IdentityError,
    IdentitySettings,
    scope_act,
    scope_dangerous,
    scope_read,
    valid_name,
)
from thingwire.identity_gate import Gatekeeper, TheAuthClient

DEV = "desk-bot"
TD = {
    "@context": "https://www.w3.org/2019/wot/td/v1.1",
    "@type": "Thing",
    "id": f"urn:thingwire:device:{DEV}",
    "title": "Desk bot",
    "description": "bot",
    "securityDefinitions": {"n": {"scheme": "nosec"}},
    "security": ["n"],
    "properties": {
        "pan": {
            "type": "number",
            "readOnly": True,
            "description": "pan angle",
            "forms": [{"href": "mqtt://b/t", "op": "observeproperty"}],
        }
    },
    "actions": {
        "look_at": {
            "title": "Look",
            "description": "look",
            "safe": True,
            "idempotent": True,
            "input": {"type": "object", "properties": {"x": {"type": "number"}}, "required": ["x"]},
            "forms": [{"href": "mqtt://b/c", "op": "invokeaction"}],
        },
        "sweep": {
            "title": "Sweep",
            "description": "sweep head",
            "safe": False,
            "idempotent": False,
            "forms": [{"href": "mqtt://b/c", "op": "invokeaction"}],
        },
    },
}


def ident(*scopes: str) -> AgentIdentity:
    return AgentIdentity(AGENT, f"agent:{AGENT}", (f"agent:{AGENT}",), frozenset(scopes), "j1")


class FakeAuth(TheAuthClient):
    """Scripted identity service: open result, then a list of spend replies."""

    def __init__(
        self,
        spends: list[tuple[int, dict[str, Any]]],
        opened: tuple[int, dict[str, Any]] | None = None,
    ) -> None:
        self.spends = list(spends)
        self.opened = opened or (201, {"approval_id": "ap1"})
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None, **_: Any
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append((path, body))
        if path == "/thingwire/approvals":
            return self.opened
        reply = self.spends.pop(0) if len(self.spends) > 1 else self.spends[0]
        if reply[0] == 0:
            raise IdentityError("identity service unreachable")
        return reply


PENDING = (200, {"code": "approval_pending", "proceed": False})
GRANTED = (200, {"code": "approval_consumed", "proceed": True})


def gatekeeper(client: TheAuthClient, timeout: float = 1.0) -> Gatekeeper:
    s = IdentitySettings(
        issuer=ISS, audience=AUD, admin_token="x", approval_timeout=timeout, approval_poll=0.01
    )
    return Gatekeeper(s, client)


class TestScopes:
    def test_no_identity_denied(self) -> None:
        g = gatekeeper(FakeAuth([GRANTED]))
        assert g.check_read(None, DEV).reason == "unauthenticated"
        assert g.check_scope(None, DEV, "look_at", False).reason == "unauthenticated"

    def test_missing_act_scope(self) -> None:
        v = gatekeeper(FakeAuth([GRANTED])).check_scope(
            ident(f"device:{DEV}:read"), DEV, "sweep", True
        )
        assert not v.allowed and v.reason == "missing scope"
        assert v.required_scope == f"device:{DEV}:act:sweep"
        assert v.error()["error"] == "DENIED"

    def test_scopes_are_per_device_and_action(self) -> None:
        g = gatekeeper(FakeAuth([GRANTED]))
        have = ident(f"device:{DEV}:act:look_at", "device:other:act:sweep")
        assert g.check_scope(have, DEV, "look_at", False).allowed
        assert not g.check_scope(have, DEV, "sweep", True).allowed
        assert not g.check_read(have, DEV).allowed

    def test_dangerous_scope_only_covers_dangerous_actions(self) -> None:
        g, have = gatekeeper(FakeAuth([GRANTED])), ident(f"device:{DEV}:act:dangerous")
        assert g.check_scope(have, DEV, "sweep", True).allowed
        assert not g.check_scope(have, DEV, "look_at", False).allowed

    @pytest.mark.parametrize(
        ("dev", "act"), [("a:act", "read"), ("d", "x:y"), ("d", "a b"), ("", "x")]
    )
    def test_colliding_names_denied(self, dev: str, act: str) -> None:
        # device "a:act" read == device "a" action "read" must never be confusable
        v = gatekeeper(FakeAuth([GRANTED])).check_scope(ident("device:a:act:read"), dev, act, False)
        assert not v.allowed and "invalid" in v.reason


class TestApproval:
    async def wait(self, client: FakeAuth, timeout: float = 1.0) -> gate_mod.Verdict:
        return await gatekeeper(client, timeout).wait_approval(ident(), DEV, "sweep", {"speed": 1})

    async def test_waits_then_granted(self) -> None:
        client = FakeAuth([PENDING, PENDING, GRANTED])
        v = await self.wait(client)
        assert v.allowed and v.decision == "approved"
        spends = [c for c in client.calls if c[0].endswith("/spend")]
        assert len(spends) == 3
        # approval is bound to agent, device, action and the exact arguments
        assert spends[0][1] == {
            "agent_id": AGENT,
            "device_id": DEV,
            "action": "sweep",
            "arguments": {"speed": 1},
        }
        assert client.calls[0][1] == spends[0][1]

    async def test_timeout_denies(self) -> None:
        v = await self.wait(FakeAuth([PENDING]), timeout=0.05)
        assert not v.allowed and v.reason == "approval timeout"

    @pytest.mark.parametrize(
        "code",
        [
            "approval_denied",
            "approval_expired",
            "approval_already_used",
            "approval_arguments_mismatch",
            "approval_not_found",
            "weird",
        ],
    )
    async def test_refusals_deny(self, code: str) -> None:
        v = await self.wait(FakeAuth([PENDING, (200, {"code": code, "proceed": False})]))
        assert not v.allowed

    async def test_proceed_flag_required(self) -> None:
        v = await self.wait(FakeAuth([(200, {"code": "approval_consumed", "proceed": False})]))
        assert not v.allowed

    @pytest.mark.parametrize(
        "opened", [(500, {}), (201, {}), (401, {"error": "x"}), (201, {"approval_id": 7})]
    )
    async def test_open_failure_denies(self, opened: tuple[int, dict[str, Any]]) -> None:
        assert not (await self.wait(FakeAuth([GRANTED], opened))).allowed

    async def test_service_down_denies(self) -> None:
        assert not (await self.wait(FakeAuth([PENDING, (0, {})]))).allowed
        assert not (await self.wait(FakeAuth([(500, {})]))).allowed


class TestSettingsAndScopes:
    def test_incomplete_settings_fail_closed(self) -> None:
        with pytest.raises(IdentityError):
            IdentitySettings(issuer=ISS, audience=AUD, admin_token="").validate_complete()
        with pytest.raises(IdentityError):
            IdentitySettings(issuer="", audience=AUD, admin_token="x").validate_complete()

    def test_plain_http_only_on_loopback(self) -> None:
        with pytest.raises(IdentityError):
            IdentitySettings(
                issuer="http://auth.example", audience=AUD, admin_token="x"
            ).validate_complete()
        IdentitySettings(
            issuer="https://auth.example", audience=AUD, admin_token="x"
        ).validate_complete()
        IdentitySettings(
            issuer="http://auth.lan", audience=AUD, admin_token="x", allow_http=True
        ).validate_complete()
        IdentitySettings(issuer=ISS, audience=AUD, admin_token="x").validate_complete()

    def test_scope_strings(self) -> None:
        assert scope_read("d1") == "device:d1:read"
        assert scope_act("d1", "sweep") == "device:d1:act:sweep"
        assert scope_dangerous("d1") == "device:d1:act:dangerous"

    @pytest.mark.parametrize("bad", ["", "a:b", "a b", "a\n", "x" * 65, "../x", "a;b"])
    def test_invalid_names(self, bad: str) -> None:
        assert not valid_name(bad)


class TestAuditColumns:
    async def test_identity_lands_in_audit_columns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        audit = AuditLog(":memory:")
        await audit.initialize()
        v = gatekeeper(FakeAuth([GRANTED])).check_scope(ident(), DEV, "sweep", True)
        await gate_mod.record_audit(
            audit,
            ident=ident("s1"),
            verdict=v,
            device_id=DEV,
            action="do_sweep",
            params={},
            result={"error": "DENIED"},
        )
        row = (await audit.get_recent())[0]
        assert row["agent_id"] == AGENT and row["reason"] == "missing scope"
        assert row["decision"] == "deny" and row["scopes"] == ["s1"]
        assert row["delegation_chain"] == [f"agent:{AGENT}"]
        await audit.close()
