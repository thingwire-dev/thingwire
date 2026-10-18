"""Tests for scripts/check_dashes.py."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

EM = chr(0x2014)
EN = chr(0x2013)


def _load() -> ModuleType:
    path = Path(__file__).resolve().parent.parent / "scripts" / "check_dashes.py"
    spec = importlib.util.spec_from_file_location("check_dashes", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_dashes"] = mod
    spec.loader.exec_module(mod)
    return mod


cd = _load()


def test_find_dashes_reports_line_and_kind() -> None:
    text = f"ok line\nbad {EM} here\nalso {EN} bad\n"
    assert cd.find_dashes(text) == [(2, "em dash"), (3, "en dash")]


def test_clean_text_has_no_hits() -> None:
    assert cd.find_dashes("plain - hyphen and -- double\n") == []


def test_run_flags_file(tmp_path: Path) -> None:
    f = tmp_path / "a.md"
    f.write_text(f"x {EM} y\n", encoding="utf-8")
    errors = cd.run([str(f)], set(), None)
    assert len(errors) == 1 and "em dash" in errors[0]


def test_run_skips_allowlisted_without_base(tmp_path: Path) -> None:
    f = tmp_path / "legacy.md"
    f.write_text(f"x {EN} y\n", encoding="utf-8")
    assert cd.run([str(f)], {str(f)}, None) == []


def test_run_skips_binary_and_non_utf8(tmp_path: Path) -> None:
    b = tmp_path / "b.bin"
    b.write_bytes(b"\0" + EM.encode())
    n = tmp_path / "n.txt"
    n.write_bytes(b"\xff\xfe\xfa")
    assert cd.run([str(b), str(n)], set(), None) == []


def test_load_allowlist_ignores_comments(tmp_path: Path) -> None:
    al = tmp_path / "al.txt"
    al.write_text("# header\nfoo.md  # why\n\nbar.md\n", encoding="utf-8")
    assert cd.load_allowlist(al) == {"foo.md", "bar.md"}


def test_main_exit_codes(tmp_path: Path) -> None:
    bad = tmp_path / "bad.md"
    bad.write_text(f"a {EM} b\n", encoding="utf-8")
    good = tmp_path / "good.md"
    good.write_text("fine\n", encoding="utf-8")
    none = str(tmp_path / "no-allowlist")
    assert cd.main(["--allowlist", none, str(bad)]) == 1
    assert cd.main(["--allowlist", none, str(good)]) == 0
