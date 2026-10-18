"""The desk bot firmware TD (generated from device-deskbot.yaml) must match the library TD."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GEN_PATH = ROOT / "firmware" / "tools" / "gen_deskbot_td.py"
LIB_TD = ROOT / "td-library" / "desk-bot.td.json"
HEADER = ROOT / "firmware" / "src" / "deskbot_gen.h"


def _load_gen():
    spec = importlib.util.spec_from_file_location("gen_deskbot_td", GEN_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gen_deskbot_td"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_generated_header_is_current() -> None:
    gen = _load_gen()
    assert HEADER.read_text() == gen.render_header(gen.load_config())


def test_header_embeds_the_same_td_as_the_generator() -> None:
    gen = _load_gen()
    match = re.search(r'R"TW\((.*)\)TW"', HEADER.read_text(), re.S)
    assert match
    assert json.loads(match.group(1)) == gen.build_td(gen.load_config())


def test_td_has_contract_surface() -> None:
    gen = _load_gen()
    td = gen.build_td(gen.load_config())
    assert set(td["properties"]) == {"head_pan", "head_tilt", "expression", "person_detected"}
    assert set(td["actions"]) == {"look_at", "set_expression", "nod", "sweep"}
    assert set(td["events"]) == {"wake_word", "person_seen"}
    assert td["actions"]["sweep"]["safe"] is False
    assert td["properties"]["head_pan"]["maxSlewDegPerSec"] == 90
    assert td["properties"]["head_tilt"]["minimum"] == -30


def test_td_matches_td_library() -> None:
    if not LIB_TD.exists():
        pytest.skip("td-library/desk-bot.td.json not on this branch yet (W1); rerun after merge")
    gen = _load_gen()
    assert gen.build_td(gen.load_config()) == json.loads(LIB_TD.read_text())
