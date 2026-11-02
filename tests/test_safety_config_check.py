"""safety_config.yaml is validated at start and never replaced by defaults (contracts 5.8)."""

from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

from thingwire.safety import SafetyLayer
from thingwire.safety_config_check import ConfigError, check_config

ROOT = Path(__file__).resolve().parent.parent


def load(tmp_path: Path, text: str) -> SafetyLayer:
    path = tmp_path / "safety.yaml"
    path.write_text(text)
    return SafetyLayer(str(path))


def problems(config: Any) -> list[str]:
    with pytest.raises(ConfigError) as err:
        check_config(config)
    return err.value.problems


def test_the_shipped_config_and_the_defaults_are_valid() -> None:
    check_config(yaml.safe_load((ROOT / "safety_config.yaml").read_text()))
    demo = ROOT / "examples" / "injection-demo" / "safety_config.yaml"
    check_config(yaml.safe_load(demo.read_text()))
    check_config(None)
    check_config({})


def test_unparseable_yaml_stops_the_start_instead_of_using_defaults(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as err:
        load(tmp_path, "global: [unclosed\n")
    assert "cannot be read as YAML" in str(err.value)


def test_a_missing_file_still_means_defaults(tmp_path: Path) -> None:
    layer = SafetyLayer(str(tmp_path / "absent.yaml"))
    assert layer.config["global"]["require_confirmation_for_dangerous"] is True


def test_empty_file_means_no_overrides(tmp_path: Path) -> None:
    assert load(tmp_path, "").config == {}


@pytest.mark.parametrize(
    ("config", "needle"),
    [
        ([1, 2], "top level: must be a mapping"),
        ({"gloabl": {}}, "unknown key 'gloabl'"),
        ({"global": {"denial_flod": {}}}, "global: unknown key 'denial_flod'"),
        ({"global": {"require_confirmation_for_dangerous": "false"}}, "true or false"),
        ({"global": {"deadman_switch_timeout_seconds": 0}}, "positive number"),
        ({"global": {"deadman_switch_timeout_seconds": True}}, "positive number"),
        ({"global": {"audit_log_retention_days": -1}}, "non negative"),
        ({"global": {"out_of_range": "wrap"}}, "'reject' or 'clamp'"),
        ({"global": {"default_rate_limit": {"max_calls": 0, "window_seconds": 60}}}, "max_calls"),
        ({"global": {"default_rate_limit": {"max_calls": 5}}}, "window_seconds"),
        ({"global": {"device_rate_limit": 5}}, "must be a mapping"),
        ({"global": {"estop": ["operator"]}}, "global.estop: must be a mapping"),
        ({"global": "on"}, "global: not a mapping"),
        ({"devices": ["bot"]}, "devices: must be a mapping"),
        ({"devices": {"bot": {"allowed_action": []}}}, "devices.bot: unknown key"),
        ({"devices": {"bot": {"allowed_actions": "do_nod"}}}, "list of action names"),
        ({"devices": {"bot": {"dangerous_actions": [1]}}}, "list of action names"),
        ({"devices": {"bot": {"rate_limits": {"do_nod": {"max_calls": "x"}}}}}, "max_calls"),
        ({"devices": {"bot": {"td_unpinned": "yes"}}}, "true or false"),
        ({"devices": {"bot": "x"}}, "devices.bot: must be a mapping"),
    ],
)
def test_each_misconfiguration_is_named(config: Any, needle: str) -> None:
    assert any(needle in p for p in problems(config)), problems(config)


def test_every_problem_is_listed_at_once() -> None:
    found = problems({"gloabl": {}, "global": {"out_of_range": "wrap", "estop": 1}})
    assert len(found) == 3


def test_valid_overrides_are_accepted(tmp_path: Path) -> None:
    layer = load(
        tmp_path,
        "global:\n  out_of_range: Clamp\n"
        "  default_rate_limit: {max_calls: 3, window_seconds: 1.5}\n"
        "devices:\n  bot:\n    allowed_actions: [do_nod]\n    rate_limits:\n"
        "      do_nod: {max_calls: 1, window_seconds: 10}\n",
    )
    assert layer.config["devices"]["bot"]["allowed_actions"] == ["do_nod"]


def test_config_error_is_a_usage_error_with_exit_status_two() -> None:
    err = ConfigError(["a", "b"])
    assert err.exit_code == 2 and "  - a\n  - b" in err.format_message()
