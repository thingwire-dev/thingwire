"""Desk bot behaviour loop in simulation: determinism, transitions and the safety path."""

import dataclasses
import itertools
import random
import sys
from pathlib import Path
from typing import Any

DEMO = Path(__file__).resolve().parent.parent / "examples" / "desk-bot-behaviour"
sys.path.insert(0, str(DEMO))

from beh_actuator import AGENT_SCOPES, GatewayActuator  # noqa: E402
from beh_chooser import RuleChooser  # noqa: E402
from beh_machine import ActResult, BehaviourLoop, Entry  # noqa: E402
from beh_sim import DEFAULT_DURATION, Sim, build_sim  # noqa: E402
from inj_tds import DESK  # noqa: E402

SEED = 7
SPAN = 70.0


def states(timeline: list[Entry]) -> list[str]:
    return [e.detail.split()[0] for e in timeline if e.kind == "state"]


def kinds(timeline: list[Entry], kind: str) -> list[Entry]:
    return [e for e in timeline if e.kind == kind]


async def run(seed: int = SEED, duration: float = DEFAULT_DURATION) -> list[Entry]:
    return await build_sim(seed).run(duration)


async def test_same_seed_gives_identical_timeline() -> None:
    assert await run(SEED) == await run(SEED)


async def test_different_seed_differs() -> None:
    assert await run(SEED) != await run(SEED + 1)


async def test_scripted_pir_drives_the_states() -> None:
    tl = await run(duration=SPAN)
    seq = states(tl)
    assert seq[:5] == ["IDLE", "NOTICE", "REACT", "COOLDOWN", "IDLE"]
    notices = [e.t for e in kinds(tl, "state") if e.detail == "NOTICE"]
    # presence at 10 to 16 s and 60 to 70 s count; flapping at 40 to 42.5 s does not
    assert len(notices) == 2
    assert 10.0 < notices[0] < 13.0 and 60.0 < notices[1] < 63.0
    assert any(e.kind == "say" or (e.kind == "act" and "nod" in e.detail) for e in tl)


async def test_long_quiet_sleeps_and_motion_wakes() -> None:
    sim = build_sim(SEED)
    tl = await sim.run(260.0, schedule=((10.0, 14.0), (200.0, 206.0)))
    seq = states(tl)
    assert "SLEEP" in seq
    assert seq[seq.index("SLEEP") + 1] == "NOTICE"
    assert sim.adapter.bot.expression != "sleepy"


async def test_no_state_change_inside_sleep_without_motion() -> None:
    tl = await build_sim(SEED).run(200.0, schedule=((5.0, 9.0),))
    seq = states(tl)
    assert seq.count("SLEEP") == 1 and seq[-1] == "SLEEP"


async def test_every_command_goes_through_the_gateway_audit() -> None:
    sim = build_sim(SEED)
    tl = await sim.run(SPAN)
    attempts = len(kinds(tl, "act")) + len(kinds(tl, "denied"))
    assert attempts > 0
    assert len(sim.arm.audit.rows) == attempts
    assert len(sim.adapter.sent) == len(kinds(tl, "act"))


async def test_scopes_are_least_privilege() -> None:
    sim = build_sim(SEED)
    assert set(AGENT_SCOPES) == {
        f"device:{DESK}:act:{a}" for a in ("look_at", "set_expression", "nod")
    }
    out = await sim.arm.invoke("do_sweep", {"speed_deg_s": 60}, "operator")
    assert not out.delivered and out.code == "MISSING_SCOPE"
    assert sim.adapter.sent == []


async def test_denied_command_is_refused_and_unknown_action_fails_closed() -> None:
    sim = build_sim(SEED)
    act = GatewayActuator(sim.arm)
    res = await act.act("look_at", {"pan": 500.0, "tilt": 0.0})
    assert not res.ok and res.code != "OK"
    assert await act.act("sweep", {"speed_deg_s": 60}) == ActResult(False, "UNKNOWN_ACTION")
    assert sim.adapter.sent == []


async def test_safety_denials_are_survived_with_backoff() -> None:
    p = build_sim(SEED).personality
    pushy = dataclasses.replace(
        p,
        look_pan=90.0,
        look_every=(0.5, 0.5),
        min_action_gap_s=0.1,
        backoff_initial_s=2.0,
        backoff_max_s=8.0,
    )
    sim = build_sim(SEED, personality=pushy)
    tl = await sim.run(120.0)
    denied = kinds(tl, "denied")
    assert denied and not sim.loop.halted
    assert any(e.t > 100.0 for e in tl)
    # a pause follows every denial, so there is never a tight retry loop
    times = [e.t for e in denied]
    assert all(b - a >= pushy.backoff_initial_s for a, b in itertools.pairwise(times))
    assert len(kinds(tl, "backoff")) == len(denied)
    for sent in sim.adapter.sent:  # nothing the safety layer denied reached the device
        assert sent.executed


async def test_estop_halts_everything_and_stays_halted() -> None:
    sim = build_sim(SEED)
    tripped: list[float] = []

    async def hook(t: float, s: Sim) -> None:
        if t >= 20.0 and not tripped:
            s.arm.safety.trip_estop(DESK, "test stop", "test")
            tripped.append(t)

    tl = await sim.run(120.0, hook=hook)
    assert sim.loop.halted and tl[-1].kind == "halt"
    sent = len(sim.adapter.sent)
    for _ in range(40):  # PIR keeps firing and time keeps moving: still nothing
        sim.clock.advance(1.0)
        await sim.loop.tick(True)
    assert len(sim.adapter.sent) == sent and sim.loop.halted
    assert sim.arm.safety.estop_latched(DESK)


class QuietHeart:
    """Wraps the real actuator but beats the deadman only once, as if the loop hung."""

    def __init__(self, inner: Any) -> None:
        self._inner, self._beats = inner, 0

    async def act(self, action: str, params: dict[str, Any]) -> ActResult:
        return await self._inner.act(action, params)  # type: ignore[no-any-return]

    def heartbeat(self) -> None:
        if self._beats == 0:
            self._inner.heartbeat()
        self._beats += 1

    def estopped(self) -> bool:
        return bool(self._inner.estopped())


LATE = ((350.0, 358.0),)


async def test_heartbeat_keeps_the_deadman_alive() -> None:
    sim = build_sim(SEED)
    tl = await sim.run(380.0, schedule=LATE)
    assert not sim.loop.halted
    assert any(e.kind == "act" and e.t > 340.0 for e in tl)
    assert not any("DEADMAN" in e.detail for e in tl)


async def test_without_heartbeats_the_deadman_trips_and_loop_halts() -> None:
    sim = build_sim(SEED, wrap=QuietHeart)
    tl = await sim.run(380.0, schedule=LATE)
    assert sim.loop.halted and tl[-1].kind == "halt"
    assert any("DEADMAN" in e.detail or "ESTOP" in e.detail for e in tl)


class Exploding:
    def __init__(self) -> None:
        self.calls = 0

    async def act(self, action: str, params: dict[str, Any]) -> ActResult:
        self.calls += 1
        raise RuntimeError("boom")

    def heartbeat(self) -> None:
        pass

    def estopped(self) -> bool:
        return False


async def test_exception_fails_closed_and_is_not_retried() -> None:
    sim = build_sim(SEED)
    boom = Exploding()
    rng = random.Random(SEED)
    chooser = RuleChooser(rng, sim.personality.utterance_p)
    loop = BehaviourLoop(sim.personality, boom, chooser, rng, sim.clock)
    for _ in range(200):
        sim.clock.advance(sim.personality.tick_s)
        await loop.tick(True)
    assert loop.halted and boom.calls == 1
    assert [e.kind for e in loop.timeline][-1] == "halt"
