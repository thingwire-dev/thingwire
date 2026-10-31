"""Command freshness: timestamp window and nonce replay cache (contracts 5.3)."""

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from tests.test_e2e_wiring import DEV, build, call, desk_td
from thingwire.config import GatewayConfig
from thingwire.mqtt_bridge import ConnectionState, MqttBridge
from thingwire.safety import SafetyLayer
from thingwire.safety_freshness import FreshnessConfig

NOW = 1_761_900_000.0
GOOD = "abcdef0123456789"


class Epoch:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> float:
        return self.now


def layer(**cfg: Any) -> tuple[SafetyLayer, Epoch]:
    epoch = Epoch()
    sl = SafetyLayer(epoch_clock=epoch)
    sl._config["global"]["command_freshness"] = cfg
    sl._fresh = FreshnessConfig.from_config(sl._config["global"]).guard(epoch)
    sl.register_device(DEV, desk_td())
    return sl, epoch


def verdict(sl: SafetyLayer, issued_at: Any, nonce: Any) -> tuple[bool, str, str]:
    d = sl.check_freshness(DEV, "do_look_at", issued_at, nonce)
    return (d is None, "OK" if d is None else d.code, "" if d is None else d.details["reason"])


def test_fresh_stamp_passes_once() -> None:
    sl, _ = layer()
    assert verdict(sl, NOW - 1, GOOD) == (True, "OK", "")


def test_same_nonce_is_a_replay_inside_the_window() -> None:
    sl, epoch = layer()
    assert verdict(sl, NOW, GOOD)[0]
    epoch.now += 10
    assert verdict(sl, epoch.now, GOOD) == (False, "REPLAYED_COMMAND", "nonce_reused")


def test_nonce_is_scoped_to_the_device() -> None:
    sl, _ = layer()
    assert verdict(sl, NOW, GOOD)[0]
    assert sl.check_freshness("other", "do_look_at", NOW, GOOD) is None


def test_hour_old_command_is_stale() -> None:
    sl, _ = layer()
    assert verdict(sl, NOW - 3600, GOOD) == (False, "STALE_COMMAND", "too_old")


def test_edges_of_the_window() -> None:
    sl, _ = layer(max_age_seconds=30, max_future_skew_seconds=5)
    assert verdict(sl, NOW - 30, "nonce-edge-01")[0]
    assert verdict(sl, NOW - 30.001, "nonce-edge-02")[1] == "STALE_COMMAND"
    assert verdict(sl, NOW + 5, "nonce-edge-03")[0]
    assert verdict(sl, NOW + 5.001, "nonce-edge-04") == (False, "STALE_COMMAND", "from_future")


def test_stale_stamp_does_not_burn_its_nonce() -> None:
    sl, _ = layer()
    assert not verdict(sl, NOW - 3600, GOOD)[0]
    assert verdict(sl, NOW, GOOD)[0]


@pytest.mark.parametrize(
    "issued_at",
    [True, "123", float("nan"), float("inf"), None, [1], {"t": 1}],
)
def test_malformed_timestamp_fails_closed(issued_at: Any) -> None:
    sl, _ = layer()
    assert verdict(sl, issued_at, GOOD)[1:] == ("STALE_COMMAND", "malformed")


@pytest.mark.parametrize("nonce", ["short", "x" * 129, "has space!!", "", 12345678, None])
def test_malformed_nonce_fails_closed(nonce: Any) -> None:
    sl, _ = layer()
    assert verdict(sl, NOW, nonce)[1:] == ("STALE_COMMAND", "malformed")


def test_half_a_stamp_is_malformed() -> None:
    sl, _ = layer()
    assert verdict(sl, NOW, None)[1:] == ("STALE_COMMAND", "malformed")
    assert verdict(sl, None, GOOD)[1:] == ("STALE_COMMAND", "malformed")


def test_no_stamp_passes_unless_required() -> None:
    sl, _ = layer()
    assert sl.check_freshness(DEV, "do_look_at", None, None) is None
    sl, _ = layer(required=True)
    d = sl.check_freshness(DEV, "do_look_at", None, None)
    assert d is not None and (d.code, d.details["reason"]) == ("STALE_COMMAND", "missing")


def test_disabled_skips_everything() -> None:
    sl, _ = layer(enabled=False, required=True)
    assert sl.check_freshness(DEV, "do_look_at", NOW - 9999, "x") is None


def test_nonce_expires_after_its_window() -> None:
    sl, epoch = layer(max_age_seconds=10, max_future_skew_seconds=0, nonce_window_seconds=20)
    assert verdict(sl, NOW, GOOD)[0]
    epoch.now += 21
    assert verdict(sl, epoch.now, GOOD)[0]


def test_window_is_never_shorter_than_the_validity() -> None:
    cfg = FreshnessConfig.from_config(
        {
            "command_freshness": {
                "max_age_seconds": 60,
                "max_future_skew_seconds": 5,
                "nonce_window_seconds": 1,
            }
        }
    )
    assert cfg.nonce_window_seconds == 65


def test_full_cache_fails_closed_and_never_evicts() -> None:
    sl, _ = layer(max_nonces=3)
    for i in range(3):
        assert verdict(sl, NOW, f"nonce-{i}-0000")[0]
    d = sl.check_freshness(DEV, "do_look_at", NOW, "nonce-new-0000")
    assert d is not None and (d.code, d.details["reason"]) == (
        "SAFETY_CHECK_ERROR",
        "nonce_cache_full",
    )
    assert verdict(sl, NOW, "nonce-0-0000")[1] == "REPLAYED_COMMAND"  # nothing was evicted


def test_expired_entries_free_the_cache() -> None:
    sl, epoch = layer(
        max_nonces=2, max_age_seconds=5, max_future_skew_seconds=0, nonce_window_seconds=5
    )
    assert verdict(sl, NOW, "nonce-a-0000")[0] and verdict(sl, NOW, "nonce-b-0000")[0]
    epoch.now += 6
    assert verdict(sl, epoch.now, "nonce-c-0000")[0]


@pytest.mark.parametrize(
    "bad",
    [
        {"max_age_seconds": -1},
        {"max_age_seconds": "soon"},
        {"max_nonces": 0},
        {"max_future_skew_seconds": True},
        {"enabled": "yes"},
    ],
)
def test_malformed_config_is_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        FreshnessConfig.from_config({"command_freshness": bad})


def test_defaults_come_from_config_not_constants_in_the_guard() -> None:
    cfg = FreshnessConfig.from_config({"command_freshness": {"max_age_seconds": 7}})
    assert (cfg.max_age_seconds, cfg.enabled, cfg.required) == (7.0, True, False)


def test_denial_is_deterministic_for_a_replayed_run() -> None:
    def run() -> list[tuple[bool, str, str]]:
        sl, epoch = layer()
        out = [verdict(sl, NOW, GOOD), verdict(sl, NOW, GOOD), verdict(sl, NOW - 99, "n-other-000")]
        epoch.now += 100
        out.append(verdict(sl, NOW, "n-third-0000"))
        return out

    assert run() == run()


# --- through the MCP tools and the gateway pipeline -------------------------


async def test_replayed_stamped_call_never_reaches_the_device() -> None:
    epoch = Epoch()
    mcp, bridge, audit, _ = build(SafetyLayer(epoch_clock=epoch))
    args = {"pan": 10, "tilt": 0, "issued_at": NOW, "nonce": GOOD}
    first = await call(mcp, "do_look_at", args)
    assert first["status"] == "command_sent"
    assert bridge.sent[0]["value"] == {"pan": 10, "tilt": 0}  # stamp never reaches the device
    second = await call(mcp, "do_look_at", args)
    assert second["error"] == "REPLAYED_COMMAND"
    assert len(bridge.sent) == 1
    assert audit.rows[-1]["reason"] == "REPLAYED_COMMAND"


async def test_hour_old_stamped_call_is_refused() -> None:
    epoch = Epoch()
    mcp, bridge, _, _ = build(SafetyLayer(epoch_clock=epoch))
    out = await call(
        mcp, "do_look_at", {"pan": 45, "tilt": 0, "issued_at": NOW - 3600, "nonce": GOOD}
    )
    assert out["error"] == "STALE_COMMAND" and not bridge.sent


async def test_unstamped_call_still_works_by_default() -> None:
    mcp, bridge, _, _ = build(SafetyLayer(epoch_clock=Epoch()))
    assert (await call(mcp, "do_look_at", {"pan": 5, "tilt": 0}))["status"] == "command_sent"
    assert len(bridge.sent) == 1


async def test_required_mode_refuses_an_unstamped_call() -> None:
    safety = SafetyLayer(epoch_clock=Epoch())
    safety._config["global"]["command_freshness"] = {"required": True}
    safety._fresh = FreshnessConfig.from_config(safety._config["global"]).guard(Epoch())
    mcp, bridge, _, _ = build(safety)
    out = await call(mcp, "do_look_at", {"pan": 5, "tilt": 0})
    assert out["error"] == "STALE_COMMAND" and not bridge.sent


async def test_slow_approval_does_not_age_the_command() -> None:
    """Freshness is judged before the approval wait, so a dangerous call can still be approved."""
    epoch = Epoch()
    mcp, bridge, _, _ = build(SafetyLayer(epoch_clock=epoch))
    out = await call(mcp, "do_sweep", {"speed_deg_s": 60, "issued_at": NOW, "nonce": GOOD})
    assert out["error"] == "APPROVAL_REQUIRED"  # stdio mode denies approvals; not STALE_COMMAND
    assert not bridge.sent


async def test_outbound_command_carries_issued_at() -> None:
    bridge = MqttBridge(GatewayConfig(device_topic_prefix="thingwire"))
    bridge._client = MagicMock()
    bridge._client.publish.return_value.rc = 0
    bridge._state = ConnectionState.CONNECTED
    bridge.wall_clock = lambda: NOW
    action_id = await bridge.send_command(DEV, "look_at", "look_at", {"pan": 1, "tilt": 2})
    cmd = json.loads(bridge._client.publish.call_args.args[1])
    assert cmd["issued_at"] == NOW and cmd["action_id"] == action_id
