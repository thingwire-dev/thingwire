"""Virtual desk bot show_text and blink: limits mirror the firmware config (contracts 1.6, 2.3)."""

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from desk_bot_display import load_display_limits  # noqa: E402
from virtual_desk_bot import DeskBot  # noqa: E402

LIMITS = load_display_limits()


def cmd(name: str, **value: Any) -> dict[str, Any]:
    return {"action_id": "a1", "command": name, "target": name, "value": value}


def error_of(bot: DeskBot, name: str, **value: Any) -> str:
    ack = bot.apply_command(cmd(name, **value))
    assert ack["status"] == "error", ack
    return str(ack["error"])


def test_show_text_then_expiry_restores_expression() -> None:
    bot = DeskBot()
    bot.apply_command(cmd("set_expression", expression="happy"))
    assert bot.apply_command(cmd("show_text", text="hello", duration_ms=500))["status"] == "ok"
    assert bot.text == "hello"
    bot.step(0.4)
    assert bot.text == "hello"
    bot.step(0.2)
    assert bot.text is None and bot.expression == "happy"


def test_default_duration_comes_from_config() -> None:
    bot = DeskBot()
    bot.apply_command(cmd("show_text", text="hi"))
    assert bot.text_left_s == pytest.approx(LIMITS["text_default_ms"] / 1000)


def test_new_text_replaces_and_expression_change_clears() -> None:
    bot = DeskBot()
    bot.apply_command(cmd("show_text", text="one"))
    bot.apply_command(cmd("show_text", text="two", duration_ms=100))
    assert bot.text == "two"
    bot.apply_command(cmd("set_expression", expression="alert"))
    assert bot.text is None and bot.expression == "alert"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"text": "x" * (LIMITS["text_max_chars"] + 1)}, "text too long"),
        ({"text": "  "}, "text blank"),
        ({"text": "a\nb"}, "text charset"),
        ({"text": "café"}, "text charset"),
        ({"text": 5}, "text charset"),
        ({}, "text charset"),
        ({"text": "hi", "duration_ms": 0}, "duration out of range"),
        ({"text": "hi", "duration_ms": LIMITS["text_max_ms"] + 1}, "duration out of range"),
        ({"text": "hi", "duration_ms": 1.5}, "duration out of range"),
    ],
)
def test_show_text_errors_are_fixed_strings(value: dict[str, Any], message: str) -> None:
    bot = DeskBot()
    assert error_of(bot, "show_text", **value) == message
    assert bot.text is None


def test_show_text_boundary_is_accepted() -> None:
    bot = DeskBot()
    text = "z" * LIMITS["text_max_chars"]
    assert (
        bot.apply_command(cmd("show_text", text=text, duration_ms=LIMITS["text_max_ms"]))["status"]
        == "ok"
    )


def test_blink_runs_and_finishes() -> None:
    bot = DeskBot()
    assert bot.apply_command(cmd("blink", times=2, speed="slow"))["status"] == "ok"
    assert bot.blink_left_s > 0
    bot.step(10)
    assert bot.blink_left_s == 0 and bot.expression == "neutral"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"times": 0}, "times out of range"),
        ({"times": LIMITS["blink_max_times"] + 1}, "times out of range"),
        ({"times": True}, "times out of range"),
        ({"times": 1, "speed": "fast"}, "unknown speed"),
    ],
)
def test_blink_errors(value: dict[str, Any], message: str) -> None:
    assert error_of(DeskBot(), "blink", **value) == message


def test_blink_refused_while_text_visible_and_text_cancels_blink() -> None:
    bot = DeskBot()
    bot.apply_command(cmd("show_text", text="hi"))
    assert error_of(bot, "blink", times=1) == "display busy"
    bot = DeskBot()
    bot.apply_command(cmd("blink", times=3))
    bot.apply_command(cmd("show_text", text="hi"))
    assert bot.blink_left_s == 0


def test_estop_refuses_and_trip_clears_display() -> None:
    bot = DeskBot()
    bot.apply_command(cmd("show_text", text="hi"))
    bot.trip_estop()
    assert bot.text is None
    for name, value in (("show_text", {"text": "x"}), ("blink", {"times": 1})):
        ack = bot.apply_command(cmd(name, **value))
        assert ack["status"] == "error" and ack["error"] == "ESTOP_LATCHED"
    assert bot.text is None


def test_ack_never_echoes_text() -> None:
    ack = DeskBot().apply_command(cmd("show_text", text="secret words"))
    assert "secret" not in json.dumps(ack)


def test_firmware_config_td_and_header_agree() -> None:
    td = json.loads((ROOT / "td-library" / "desk-bot.td.json").read_text())
    header = (ROOT / "firmware" / "src" / "deskbot_gen.h").read_text()

    def define(name: str) -> int:
        match = re.search(rf"#define {name} (\d+)", header)
        assert match
        return int(match.group(1))

    show = td["actions"]["show_text"]["input"]["properties"]
    assert show["text"]["maxLength"] == LIMITS["text_max_chars"] == define("DESKBOT_TEXT_MAX_CHARS")
    assert show["duration_ms"]["maximum"] == LIMITS["text_max_ms"] == define("DESKBOT_TEXT_MAX_MS")
    blink = td["actions"]["blink"]["input"]["properties"]["times"]["maximum"]
    assert blink == LIMITS["blink_max_times"] == define("DESKBOT_BLINK_MAX_TIMES")
