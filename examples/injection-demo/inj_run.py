"""Runs corpus cases and benign tasks against an arm and aggregates the results."""

import asyncio
from dataclasses import asdict, dataclass
from typing import Any

from inj_agent import NaiveAgent, operator_steps
from inj_arms import Arm, BaselineArm, Registry
from inj_benign import BENIGN
from inj_corpus import CASES
from inj_gateway import CONFIG_PATH, DEFAULT_SCOPES, GatewayArm
from inj_judge import harmful
from inj_model import CATEGORIES, Benign, Case, Delivery, Event, HumanMode, Step
from inj_tds import DESK, RELAY, desk_td, poison, relay_td
from inj_world import DeviceAdapter, IdSource, SimClock

from thingwire.safety import SafetyLayer

CALL_DT = 0.5  # simulated seconds an agent spends per tool call
ARMS = ("baseline", "gateway")


def rate_max() -> int:
    """Calls per window from the bench safety config, so the judge shares the gateway's number."""
    cfg = SafetyLayer(str(CONFIG_PATH)).config
    return int(cfg["global"]["default_rate_limit"]["max_calls"])


@dataclass
class Run:
    arm: Arm
    adapter: DeviceAdapter
    clock: SimClock
    agent: NaiveAgent
    alive: bool = True


def build_run(
    arm_name: str, seed: int, scopes: tuple[str, ...], human: HumanMode, patch: str
) -> Run:
    clock, ids = SimClock(), IdSource(seed)
    adapter = DeviceAdapter(clock)
    registry = Registry({DESK: poison(desk_td(), patch), RELAY: relay_td()})
    arm: Arm
    if arm_name == "gateway":
        arm = GatewayArm(registry, adapter, clock, ids, DEFAULT_SCOPES + scopes, human)
    else:
        arm = BaselineArm(registry, adapter)
    arm.heartbeat()
    run = Run(arm, adapter, clock, agent=None)  # type: ignore[arg-type]

    def tick() -> None:
        advance(run, CALL_DT)

    run.agent = NaiveAgent(arm, tick)
    return run


def advance(run: Run, seconds: float) -> None:
    run.clock.advance(seconds)
    if run.alive:  # a live device sends telemetry, which is the deadman heartbeat
        run.arm.heartbeat()


async def play(run: Run, event: Event) -> None:
    if event.kind == "say":
        await run.agent.ingest(event.channel, event.text)
    elif event.kind == "mqtt":
        text = " | ".join(str(v) for v in event.payload.values() if isinstance(v, str))
        await run.agent.ingest("mqtt_payload", text)
    elif event.kind == "read_tools":
        for text in run.arm.describe(event.device):
            await run.agent.ingest("td_description", text)
    elif event.kind == "advance":
        advance(run, event.seconds)
    elif event.kind == "silence":
        run.alive = False
    elif event.kind == "estop":
        run.adapter.trip_device_estop()
        run.arm.on_estop(event.device)
    elif event.kind == "set_reading":
        run.adapter.readings[(event.device, event.prop)] = event.value


async def play_all(run: Run, events: tuple[Event, ...]) -> None:
    for event in events:
        await play(run, event)


def _steps(steps: list[Step]) -> list[dict[str, Any]]:
    return [asdict(s) for s in steps]


def _deliveries(ds: list[Delivery]) -> list[dict[str, Any]]:
    return [{k: v for k, v in asdict(d).items() if k != "at"} for d in ds]


def run_case(case: Case, arm_name: str, seed: int) -> dict[str, Any]:
    """One attack on one arm. Returns a JSON-ready record."""
    run = build_run(arm_name, seed, case.scopes, case.human, case.td_patch)
    asyncio.run(play_all(run, case.events))
    bad = harmful(case.harm, run.adapter.sent, desk_td(), rate_max())
    untrusted = [s for s in run.agent.steps if s.channel != "operator"]
    blocked = sorted({s.code for s in untrusted if not s.ok and s.code != "NO_GATEWAY"})
    return {
        "id": case.id,
        "category": case.category,
        "arm": arm_name,
        "attack_success": bool(bad),
        "harmful_deliveries": _deliveries(bad),
        "executed_on_device": sum(d.executed for d in bad),
        "blocked_codes": blocked,
        "allowed_codes": sorted({s.code for s in untrusted if s.delivered}),
        "steps": _steps(run.agent.steps),
    }


def run_benign(task: Benign, arm_name: str, seed: int) -> dict[str, Any]:
    """One legitimate task on one arm. It passes when every operator call succeeds."""
    run = build_run(arm_name, seed, task.scopes, task.human, "")
    asyncio.run(play_all(run, task.events))
    steps = operator_steps(run.agent.steps)
    passed = len(steps) == task.expected_calls and all(s.ok for s in steps)
    return {
        "id": task.id,
        "arm": arm_name,
        "passed": passed,
        "failed_codes": sorted({s.code for s in steps if not s.ok}),
        "steps": _steps(run.agent.steps),
    }


def run_all(seed: int) -> dict[str, Any]:
    """Every case and control on both arms, plus the per-category summary."""
    attacks = [run_case(c, a, seed) for a in ARMS for c in CASES]
    controls = [run_benign(b, a, seed) for a in ARMS for b in BENIGN]
    return {
        "seed": seed,
        "attacks": attacks,
        "benign": controls,
        "summary": summarize(attacks, controls),
    }


def summarize(attacks: list[dict[str, Any]], controls: list[dict[str, Any]]) -> dict[str, Any]:
    cats: dict[str, Any] = {}
    for cat in (*CATEGORIES, "ALL"):
        row: dict[str, Any] = {}
        for arm in ARMS:
            rows = [r for r in attacks if r["arm"] == arm and cat in (r["category"], "ALL")]
            row[arm] = {
                "cases": len(rows),
                "successes": sum(r["attack_success"] for r in rows),
                "executed": sum(r["executed_on_device"] > 0 for r in rows),
            }
        cats[cat] = row
    benign: dict[str, Any] = {}
    for arm in ARMS:
        rows = [r for r in controls if r["arm"] == arm]
        failed = sum(not r["passed"] for r in rows)
        benign[arm] = {"tasks": len(rows), "failed": failed}
    return {"categories": cats, "benign": benign}
