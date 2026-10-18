"""Tests for scripts/check_ownership.py."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_ownership", ROOT / "scripts" / "check_ownership.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_ownership"] = mod
    spec.loader.exec_module(mod)
    return mod


co = _load()

CONFIG: dict[str, Any] = {
    "label": "shared-file-ok",
    "skip": ["release/*"],
    "shared": ["pyproject.toml", "td-library/**"],
    "common": ["docs/agents/TASKS.md"],
    "rules": [
        {"branch": "feat/w2-*", "paths": ["src/thingwire/safety_*.py", "tests/test_safety*.py"]},
        {"branch": "docs/*", "paths": ["docs/**", "*.md"]},
    ],
}


def test_in_scope_files_pass() -> None:
    r = co.check(CONFIG, "feat/w2-motion", ["src/thingwire/safety_motion.py"], set())
    assert r.ok


def test_common_file_allowed_for_any_rule() -> None:
    assert co.check(CONFIG, "feat/w2-x", ["docs/agents/TASKS.md"], set()).ok


def test_out_of_scope_file_fails() -> None:
    r = co.check(CONFIG, "feat/w2-motion", ["src/thingwire/recorder.py"], set())
    assert not r.ok and "recorder.py" in " ".join(r.messages)


def test_shared_file_needs_label() -> None:
    r = co.check(CONFIG, "feat/w2-motion", ["pyproject.toml"], set())
    assert not r.ok and "shared-file-ok" in " ".join(r.messages)


def test_shared_file_passes_with_label_if_in_scope() -> None:
    cfg = {**CONFIG, "rules": [{"branch": "fix/*", "paths": ["**"]}]}
    assert co.check(cfg, "fix/x", ["pyproject.toml"], {"shared-file-ok"}).ok


def test_label_unlocks_shared_but_not_other_out_of_scope_files() -> None:
    assert co.check(CONFIG, "feat/w2-motion", ["pyproject.toml"], {"shared-file-ok"}).ok
    r = co.check(CONFIG, "feat/w2-motion", ["src/thingwire/recorder.py"], {"shared-file-ok"})
    assert not r.ok


def test_unknown_prefix_fails_closed_with_message() -> None:
    r = co.check(CONFIG, "wip/whatever", ["README.md"], {"shared-file-ok"})
    assert not r.ok
    text = " ".join(r.messages)
    assert "unknown branch" in text and "feat/w2-*" in text


def test_skip_branch_not_checked() -> None:
    assert co.check(CONFIG, "release/robot-stack-v0", ["pyproject.toml"], set()).ok


def test_double_star_and_single_star_semantics() -> None:
    assert co.matches("docs/a/b.md", ["docs/**"])
    assert co.matches("README.md", ["*.md"])
    assert not co.matches("docs/a.md", ["*.md"])
    assert co.matches("a/b/c.py", ["**/c.py"])
    assert co.matches("c.py", ["**/c.py"])


def test_labels_from_event(tmp_path: Path) -> None:
    ev = tmp_path / "event.json"
    ev.write_text('{"pull_request": {"labels": [{"name": "shared-file-ok"}, {"name": "x"}]}}')
    assert co.labels_from_event(str(ev)) == {"shared-file-ok", "x"}


def test_main_uses_files_and_labels() -> None:
    cfg = str(ROOT / ".agents" / "ownership.yaml")
    base = ["--config", cfg, "--branch", "feat/w2-m"]
    assert co.main([*base, "--files", "src/thingwire/safety_motion.py"]) == 0
    assert co.main([*base, "--files", "src/thingwire/safety.py"]) == 1
    assert co.main([*base, "--labels", "shared-file-ok", "--files", "src/thingwire/safety.py"]) == 0


def test_real_config_this_pr_passes() -> None:
    config = yaml.safe_load((ROOT / ".agents" / "ownership.yaml").read_text())
    files = [
        "AGENTS.md",
        "CLAUDE.md",
        ".gitignore",
        "Makefile",
        ".agents/ownership.yaml",
        ".github/workflows/ci.yml",
        ".github/CODEOWNERS",
        "docs/contracts.md",
        "docs/agents/TASKS.md",
        "docs/agents/RELEASE.md",
        "scripts/check_ownership.py",
        "scripts/agent_worktree.sh",
        "tests/test_check_ownership.py",
    ]
    assert co.check(config, "chore/agent-environment", files, set()).ok


@pytest.mark.parametrize("branch", ["feat/w1-x", "feat/w3-id", "fix/deadman", "hotfix/z"])
def test_real_config_known_branches_have_rules(branch: str) -> None:
    config = yaml.safe_load((ROOT / ".agents" / "ownership.yaml").read_text())
    assert co.check(config, branch, [], set()).ok
