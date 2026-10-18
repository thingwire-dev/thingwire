#!/usr/bin/env python3
"""Check that a PR only touches files its branch is allowed to touch.

Rules live in .agents/ownership.yaml. Fails closed: an unknown branch prefix fails.
Usage: check_ownership.py --base origin/release/x [--branch NAME] [--labels a,b]
       [--event PATH] [--files f1 f2 ...]
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

DEFAULT_CONFIG = Path(".agents/ownership.yaml")


@dataclass
class Result:
    ok: bool
    messages: list[str] = field(default_factory=list)


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a glob where `**` crosses directories and `*` does not."""
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out + r"\Z")


def matches(path: str, patterns: list[str]) -> bool:
    return any(glob_to_regex(p).match(path) for p in patterns)


def check(config: dict[str, Any], branch: str, files: list[str], labels: set[str]) -> Result:
    """Return the verdict for one PR. Pure function, no I/O."""
    if any(fnmatch.fnmatchcase(branch, p) for p in config.get("skip", [])):
        return Result(True, [f"branch '{branch}' is a skip branch, not checked"])

    rule = next(
        (r for r in config.get("rules", []) if fnmatch.fnmatchcase(branch, r["branch"])), None
    )
    if rule is None:
        known = ", ".join(r["branch"] for r in config.get("rules", []))
        return Result(
            False,
            [
                f"unknown branch '{branch}': no rule in .agents/ownership.yaml.",
                f"Known patterns: {known}",
                "Rename the branch to a known pattern or add a rule in its own PR.",
            ],
        )

    label = config.get("label", "shared-file-ok")
    shared = [f for f in files if matches(f, config.get("shared", []))]
    allowed = list(rule.get("paths", [])) + list(config.get("common", []))
    outside = [f for f in files if f not in shared and not matches(f, allowed)]

    msgs: list[str] = []
    if shared and label not in labels:
        msgs.append(f"shared files need the PR label '{label}': " + ", ".join(sorted(shared)))
    if outside:
        msgs.append(
            f"files outside the paths allowed for '{rule['branch']}': " + ", ".join(sorted(outside))
        )
    if msgs:
        msgs.append("Request the change instead of editing (AGENTS.md, shared-file protocol).")
        return Result(False, msgs)
    return Result(True, [f"{len(files)} file(s) OK for rule '{rule['branch']}'"])


def labels_from_event(path: str) -> set[str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    pr = data.get("pull_request", {})
    return {lb["name"] for lb in pr.get("labels", [])}


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def changed_files(base: str) -> list[str]:
    out = git("diff", "--name-only", "--no-renames", f"{base}...HEAD")
    return [line for line in out.splitlines() if line]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--base", help="base ref to diff against, e.g. origin/main")
    ap.add_argument("--branch", help="head branch name (default: GITHUB_HEAD_REF or current)")
    ap.add_argument("--labels", help="comma separated PR labels")
    ap.add_argument("--event", help="GitHub event payload JSON (default: GITHUB_EVENT_PATH)")
    ap.add_argument("--files", nargs="*", help="explicit file list instead of git diff")
    args = ap.parse_args(argv)

    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    branch = args.branch or os.environ.get("GITHUB_HEAD_REF") or git("branch", "--show-current")
    if not branch:
        print("ownership: cannot determine branch name", file=sys.stderr)
        return 1

    if args.files is not None:
        files = args.files
    elif args.base:
        files = changed_files(args.base)
    else:
        print("ownership: pass --base or --files", file=sys.stderr)
        return 1

    labels: set[str] = set()
    if args.labels:
        labels = {lb.strip() for lb in args.labels.split(",") if lb.strip()}
    else:
        event = args.event or os.environ.get("GITHUB_EVENT_PATH")
        if event and Path(event).is_file():
            labels = labels_from_event(event)

    result = check(config, branch, files, labels)
    for line in result.messages:
        print(("ownership OK: " if result.ok else "ownership FAIL: ") + line)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
