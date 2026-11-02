"""Simulation driver: scripted PIR, simulated clock, the virtual desk bot. No wall time."""

import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from beh_actuator import BehaviourArm, GatewayActuator, build_world, desk_expressions
from beh_chooser import Chooser, RuleChooser
from beh_machine import Actuator, BehaviourLoop, Entry
from beh_personality import Personality, load_personality
from inj_world import DeviceAdapter, SimClock

PERSONALITY = Path(__file__).resolve().parent / "personality.yaml"
Interval = tuple[float, float]
TickHook = Callable[[float, "Sim"], Awaitable[None]]

# Scripted PIR (seconds): presence, absence, flapping motion, then a long quiet period.
DEFAULT_SCHEDULE: tuple[Interval, ...] = (
    (10.0, 16.0),
    (40.0, 40.4),
    (41.0, 41.3),
    (42.0, 42.5),
    (60.0, 70.0),
)
DEFAULT_DURATION = 300.0


def pir_at(t: float, schedule: tuple[Interval, ...]) -> bool:
    return any(lo <= t < hi for lo, hi in schedule)


@dataclass
class Sim:
    clock: SimClock
    adapter: DeviceAdapter
    arm: BehaviourArm
    loop: BehaviourLoop
    personality: Personality

    async def run(
        self,
        duration: float,
        schedule: tuple[Interval, ...] = DEFAULT_SCHEDULE,
        hook: TickHook | None = None,
    ) -> list[Entry]:
        """Step the loop for `duration` simulated seconds; finishes instantly."""
        t0, dt = self.clock(), self.personality.tick_s
        while self.clock() - t0 < duration and not self.loop.halted:
            self.clock.advance(dt)
            self.adapter.bot.step(dt)
            t = self.clock() - t0
            if hook is not None:
                await hook(t, self)
            await self.loop.tick(pir_at(t, schedule))
        return self.loop.timeline


def build_sim(
    seed: int,
    personality: Personality | None = None,
    chooser: Chooser | None = None,
    wrap: Callable[[Actuator], Actuator] | None = None,
) -> Sim:
    """Wire the world, the real gateway path and the loop. Same seed, same run."""
    p = personality or load_personality(PERSONALITY, desk_expressions())
    clock, adapter, arm = build_world(seed)
    rng = random.Random(seed)
    actuator: Actuator = GatewayActuator(arm)
    if wrap is not None:  # tests wrap the real actuator, never replace the safety path
        actuator = wrap(actuator)
    loop = BehaviourLoop(p, actuator, chooser or RuleChooser(rng, p.utterance_p), rng, clock)
    return Sim(clock, adapter, arm, loop, p)
