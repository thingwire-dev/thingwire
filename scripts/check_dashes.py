#!/usr/bin/env python3
"""Fail on em dash (U+2014) or en dash (U+2013) in tracked text files.

Files listed in .agents/dash-allowlist.txt are legacy debt: only lines ADDED relative to
--base are checked in them, so the count can only go down. Usage:
  check_dashes.py [--base origin/main] [--allowlist PATH] [paths ...]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BAD = {chr(0x2014): "em dash", chr(0x2013): "en dash"}
DEFAULT_ALLOWLIST = Path(".agents/dash-allowlist.txt")


def find_dashes(text: str) -> list[tuple[int, str]]:
    """Return (line number, dash name) for every offending line."""
    hits: list[tuple[int, str]] = []
    for n, line in enumerate(text.splitlines(), start=1):
        for ch, name in BAD.items():
            if ch in line:
                hits.append((n, name))
    return hits


def read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def load_allowlist(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    entries = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            entries.add(line)
    return entries


def added_lines(base: str, path: str) -> set[int]:
    """Line numbers added in `path` relative to base (new-file numbering)."""
    out = subprocess.run(
        ["git", "diff", "-U0", "--no-color", f"{base}...HEAD", "--", path],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    nums: set[int] = set()
    for line in out.splitlines():
        if line.startswith("@@"):
            plus = line.split("+", 1)[1].split(" ", 1)[0]
            start, _, count = plus.partition(",")
            n = int(count) if count else 1
            nums.update(range(int(start), int(start) + n))
    return nums


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout
    return [line for line in out.splitlines() if line]


def run(paths: list[str], allow: set[str], base: str | None) -> list[str]:
    errors: list[str] = []
    for p in paths:
        text = read_text(Path(p))
        if text is None:
            continue
        hits = find_dashes(text)
        if not hits:
            continue
        if p in allow:
            if not base:
                continue
            fresh = added_lines(base, p)
            hits = [h for h in hits if h[0] in fresh]
        errors.extend(f"{p}:{n}: {name}" for n, name in hits)
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", help="base ref; also checks added lines in allow-listed files")
    ap.add_argument("--allowlist", default=str(DEFAULT_ALLOWLIST))
    ap.add_argument("paths", nargs="*", help="files to check (default: all tracked files)")
    args = ap.parse_args(argv)

    paths = args.paths or tracked_files()
    errors = run(paths, load_allowlist(Path(args.allowlist)), args.base)
    for e in errors:
        print(e)
    if errors:
        print(f"{len(errors)} dash(es) found. Use commas, periods, parentheses or colons.")
        return 1
    print("dash check OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
