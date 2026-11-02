"""The behaviour state machine. Pure decisions: time, randomness and the actuator are injected.

States: IDLE, NOTICE, REACT, COOLDOWN, SLEEP, plus HALTED (terminal). Every actuator
command goes through the Actuator, which in this example is the real gateway pipeline.
"""

import logging
import random
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from beh_chooser import Chooser, Menu, RuleChooser, pick
from beh_personality import Personality

logger = logging.getLogger(__name__)

HALT_CODES = frozenset({"ESTOP_LATCHED", "DEADMAN_SWITCH"})


class State(StrEnum):
    IDLE = "IDLE"
    NOTICE = "NOTICE"
    REACT = "REACT"
    COOLDOWN = "COOLDOWN"
    SLEEP = "SLEEP"
    HALTED = "HALTED"


@dataclass(frozen=True)
class ActResult:
    ok: bool
    code: str


class Actuator(Protocol):
    async def act(self, action: str, params: dict[str, Any]) -> ActResult: ...

    def heartbeat(self) -> None: ...

    def estopped(self) -> bool: ...


@dataclass(frozen=True)
class Entry:
    """One timeline line. `t` is seconds since the loop started."""

    t: float
    kind: str  # state, act, denied, backoff, blink, say, halt
    state: str
    detail: str


Command = tuple[str, dict[str, Any]]


class BehaviourLoop:
    def __init__(
        self,
        personality: Personality,
        actuator: Actuator,
        chooser: Chooser,
        rng: random.Random,
        clock: Callable[[], float],
    ) -> None:
        self.p, self._act, self._chooser = personality, actuator, chooser
        self._rng, self._clock = rng, clock
        self._rule = RuleChooser(rng, personality.utterance_p)
        self._menu = Menu.from_personality(personality)
        self._t0 = clock()
        self.timeline: list[Entry] = []
        self.state = State.IDLE
        self._queue: deque[Command] = deque()
        self._until = 0.0
        self._high_since: float | None = None
        self._armed = True
        self._last_motion = self._t0
        self._next_blink = self._t0 + self._draw(personality.blink_every)
        self._next_look = self._t0 + self._draw(personality.look_every)
        self._next_action = self._t0
        self._backoff_until = self._t0
        self._backoff = personality.backoff_initial_s
        self._expr: str | None = None
        self._log("state", "IDLE start")

    @property
    def halted(self) -> bool:
        return self.state is State.HALTED

    def _draw(self, span: tuple[float, float]) -> float:
        return self._rng.uniform(*span)

    def _log(self, kind: str, detail: str) -> None:
        self.timeline.append(Entry(round(self._clock() - self._t0, 3), kind, self.state, detail))

    async def tick(self, pir: bool) -> None:
        """Advance one step with the current (untrusted, boolean) PIR reading."""
        if self.halted:
            return
        try:
            await self._tick(pir)
        except Exception:
            logger.exception("behaviour loop failed, halting (fail closed)")
            self._halt("exception")

    def _halt(self, why: str) -> None:
        self._queue.clear()
        self._log("halt", why)
        self.state = State.HALTED

    async def _tick(self, pir: bool) -> None:
        now = self._clock()
        if self._act.estopped():
            self._halt("e-stop latched")
            return
        self._act.heartbeat()
        trigger = self._read_pir(pir, now)
        self._step_state(now, trigger)
        await self._drain(now)

    def _read_pir(self, pir: bool, now: float) -> bool:
        """Debounce: presence needs a steady high, and a low re-arms the trigger."""
        if not pir:
            self._high_since, self._armed = None, True
            return False
        self._last_motion = now
        if self._high_since is None:
            self._high_since = now
        return self._armed and now - self._high_since >= self.p.trigger_after_s

    def _step_state(self, now: float, trigger: bool) -> None:
        s, p = self.state, self.p
        if s in (State.IDLE, State.SLEEP) and trigger:
            self._enter(State.NOTICE, now)
        elif s is State.IDLE:
            if now - self._last_motion >= p.sleep_after_s:
                self._enter(State.SLEEP, now)
            else:
                self._idle_life(now)
        elif s is not State.SLEEP and now >= self._until:
            nxt = {State.NOTICE: State.REACT, State.REACT: State.COOLDOWN}
            self._enter(nxt.get(s, State.IDLE), now)

    def _idle_life(self, now: float) -> None:
        p = self.p
        if now >= self._next_blink:
            # The TD has no blink action: it is a face-side animation, logged only.
            self._log("blink", "")
            self._next_blink = now + self._draw(p.blink_every)
        if now >= self._next_look:
            pan = round(self._rng.uniform(-p.look_pan, p.look_pan), 1)
            tilt = round(self._rng.uniform(-p.look_tilt, p.look_tilt), 1)
            curious = self._rng.random() < p.curiosity
            self._queue.append(("look_at", {"pan": pan, "tilt": tilt}))
            if curious:
                self._queue.append(("set_expression", {"expression": p.curious_expr}))
            self._next_look = now + self._draw(p.look_every)

    def _enter(self, new: State, now: float) -> None:
        p = self.p
        self.state = new
        self._queue.clear()
        self._log("state", new.value)
        if new is State.NOTICE:
            self._armed = False
            self._until = now + p.notice_s
            self._queue.append(("look_at", {"pan": p.front_pan, "tilt": p.front_tilt}))
            self._queue.append(("set_expression", {"expression": p.notice_expr}))
        elif new is State.REACT:
            self._until = now + p.react_s
            self._react(now)
        elif new is State.COOLDOWN:
            self._until = now + p.cooldown_s
            self._queue.append(("set_expression", {"expression": p.cooldown_expr}))
            self._queue.append(("look_at", {"pan": 0.0, "tilt": 0.0}))
        elif new is State.SLEEP:
            self._queue.append(("set_expression", {"expression": p.sleep_expr}))
            self._queue.append(("look_at", {"pan": 0.0, "tilt": p.sleep_tilt}))
        else:  # IDLE
            self._queue.append(("set_expression", {"expression": p.idle_expr}))
            self._next_blink = now + self._draw(p.blink_every)
            self._next_look = now + self._draw(p.look_every)

    def _react(self, now: float) -> None:
        choice = pick(self._chooser, self._rule, "REACT", self._menu)
        self._queue.append(("set_expression", {"expression": choice.expression}))
        if self._rng.random() < self.p.nod_p:
            self._queue.append(("nod", {"times": self.p.nod_times}))
        if choice.utterance is not None:
            # No display action exists in the TD yet, so the line is shown by the sim only.
            self._log("say", choice.utterance)

    async def _drain(self, now: float) -> None:
        """Send at most one queued command, never inside the gap or a backoff window."""
        while self._queue and now >= self._next_action and now >= self._backoff_until:
            name, params = self._queue.popleft()
            if name == "set_expression" and params["expression"] == self._expr:
                continue
            res = await self._act.act(name, params)
            self._next_action = now + self.p.min_action_gap_s
            label = f"{name} {params} -> {res.code}"
            if res.ok:
                self._backoff = self.p.backoff_initial_s
                if name == "set_expression":
                    self._expr = str(params["expression"])
                self._log("act", label)
            else:
                self._log("denied", label)
                self._on_denied(res.code, now)
            return

    def _on_denied(self, code: str, now: float) -> None:
        if code in HALT_CODES:
            self._halt(code)
            return
        self._backoff_until = now + self._backoff
        self._log("backoff", f"{self._backoff:g}s")
        self._backoff = min(self._backoff * 2, self.p.backoff_max_s)
