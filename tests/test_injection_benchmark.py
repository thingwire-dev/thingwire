"""Pins the injection benchmark: corpus schema, determinism, benign control, baseline exposure."""

import json
import sys
from pathlib import Path
from typing import Any

import pytest

DEMO = Path(__file__).resolve().parent.parent / "examples" / "injection-demo"
sys.path.insert(0, str(DEMO))

from inj_benign import BENIGN  # noqa: E402
from inj_corpus import CASES, validate  # noqa: E402
from inj_model import CATEGORIES, CHANNELS  # noqa: E402
from inj_report import gaps, markdown  # noqa: E402
from inj_run import run_all, run_benign, run_case  # noqa: E402

SEED = 7
MIN_CASES, MIN_BENIGN = 24, 8


@pytest.fixture(scope="module")
def result() -> dict[str, Any]:
    out: dict[str, Any] = run_all(SEED)
    return out


def test_corpus_is_well_formed() -> None:
    assert validate() == []
    assert len(CASES) >= MIN_CASES and len(BENIGN) >= MIN_BENIGN


def test_every_category_and_channel_is_covered() -> None:
    assert {c.category for c in CASES} == set(CATEGORIES)
    assert {c.channel for c in CASES} >= set(CHANNELS) - {"operator"}
    assert all(sum(c.category == k for c in CASES) >= 3 for k in CATEGORIES)


def test_scene_case_uses_the_real_demo_sign() -> None:
    scene = (Path(__file__).resolve().parent.parent / "scripts" / "scene.yaml").read_text()
    sign = next(c for c in CASES if c.id == "SCN-01").events[0].text
    assert sign in scene


def test_two_runs_are_identical() -> None:
    first, second = run_all(SEED), run_all(SEED)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert markdown(first) == markdown(second)


def test_benign_control_passes_through_the_gateway(result: dict[str, Any]) -> None:
    rows = [r for r in result["benign"] if r["arm"] == "gateway"]
    assert len(rows) == len(BENIGN)
    assert [r["id"] for r in rows if not r["passed"]] == []
    assert result["summary"]["benign"]["gateway"]["failed"] == 0


def test_benign_control_passes_on_the_baseline(result: dict[str, Any]) -> None:
    assert all(r["passed"] for r in result["benign"] if r["arm"] == "baseline")


def test_baseline_is_vulnerable_to_every_case(result: dict[str, Any]) -> None:
    lost = [
        r["id"] for r in result["attacks"] if r["arm"] == "baseline" and not r["attack_success"]
    ]
    assert lost == []


def test_gateway_is_strictly_better_than_baseline(result: dict[str, Any]) -> None:
    allr = result["summary"]["categories"]["ALL"]
    assert allr["gateway"]["successes"] < allr["baseline"]["successes"]
    for cat in CATEGORIES:
        row = result["summary"]["categories"][cat]
        assert row["gateway"]["successes"] <= row["baseline"]["successes"]


def test_blocked_cases_are_stopped_by_the_expected_check(result: dict[str, Any]) -> None:
    by_id = {c.id: c for c in CASES}
    for r in result["attacks"]:
        if r["arm"] == "gateway" and not r["attack_success"]:
            assert set(r["blocked_codes"]) <= set(by_id[r["id"]].expected_codes), r["id"]
            assert r["blocked_codes"], r["id"]


def test_gaps_are_reported_not_hidden(result: dict[str, Any]) -> None:
    found = {g["id"] for g in gaps(result)}
    failed = {r["id"] for r in result["attacks"] if r["arm"] == "gateway" and r["attack_success"]}
    assert found == failed
    assert all(f in markdown(result) for f in found)


def test_gateway_run_does_not_touch_the_baseline_bypass() -> None:
    """The bypass is an example-only arm: nothing in src/ names it."""
    src = (DEMO.parent.parent / "src" / "thingwire").rglob("*.py")
    assert not any("BaselineArm" in p.read_text() for p in src)


def test_single_case_helpers_are_independent() -> None:
    a = run_case(CASES[0], "gateway", SEED)
    run_benign(BENIGN[0], "gateway", SEED)
    assert run_case(CASES[0], "gateway", SEED) == a


def test_committed_results_match_a_fresh_run(result: dict[str, Any]) -> None:
    committed = json.loads((DEMO / "results" / "results.json").read_text())
    assert committed == json.loads(json.dumps(result))
    assert (DEMO / "results" / "summary.md").read_text() == markdown(result)


ALLOWED_CONFIG_DIFF = {"taint.perception_properties", "td_trust"}


def test_bench_config_differs_from_repo_config_only_by_allowed_keys() -> None:
    import yaml

    root = Path(__file__).resolve().parent.parent
    repo = yaml.safe_load((root / "safety_config.yaml").read_text())
    bench = yaml.safe_load((DEMO / "safety_config.yaml").read_text())
    assert bench["taint"].pop("perception_properties") == ["target_bearing"]
    assert set(bench.pop("td_trust")["devices"]) == {"desk-bot", "door-relay"}
    assert bench == repo
