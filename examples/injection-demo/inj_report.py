"""Console table, JSON and markdown output. Nothing here depends on wall time."""

import json
from pathlib import Path
from typing import Any

from inj_corpus import CASES
from inj_model import CATEGORIES

BY_ID = {c.id: c for c in CASES}


def pct(row: dict[str, int]) -> str:
    if not row["cases"]:
        return "n/a"
    return f"{100 * row['successes'] / row['cases']:.0f}% ({row['successes']}/{row['cases']})"


def table(result: dict[str, Any]) -> str:
    cats = result["summary"]["categories"]
    lines = [f"{'category':<28}{'baseline ASR':>16}{'gateway ASR':>16}"]
    for cat in (*CATEGORIES, "ALL"):
        lines.append(f"{cat:<28}{pct(cats[cat]['baseline']):>16}{pct(cats[cat]['gateway']):>16}")
    ben = result["summary"]["benign"]
    for arm in ("baseline", "gateway"):
        b = ben[arm]
        rate = 100 * b["failed"] / b["tasks"]
        lines.append(
            f"benign false positives, {arm}: {b['failed']}/{b['tasks']} tasks blocked ({rate:.0f}%)"
        )
    return "\n".join(lines)


def gaps(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Gateway-arm attacks that succeeded, with the reason the gateway let them through."""
    out = []
    for r in result["attacks"]:
        if r["arm"] != "gateway" or not r["attack_success"]:
            continue
        case = BY_ID[r["id"]]
        out.append(
            {
                "id": r["id"],
                "category": r["category"],
                "expected_safe": case.expected_safe,
                "delivered": r["harmful_deliveries"],
                "decision_codes": r["allowed_codes"],
                "reason": case.gap_hint or "no check covers this case",
            }
        )
    return out


def markdown(result: dict[str, Any]) -> str:
    cats = result["summary"]["categories"]
    out = ["# Injection benchmark results", "", f"Seed: {result['seed']}", ""]
    out += ["## Attack success rate (lower is better)", ""]
    out += ["| Category | Baseline | Gateway |", "| --- | --- | --- |"]
    out += [
        f"| {c} | {pct(cats[c]['baseline'])} | {pct(cats[c]['gateway'])} |"
        for c in (*CATEGORIES, "ALL")
    ]
    for arm in ("baseline", "gateway"):
        row = cats["ALL"][arm]
        out.append("")
        out.append(
            f"{arm}: {row['successes']} successes, of which {row['executed']} also ran on the "
            "virtual bot (the bot's own checks rejected the rest)."
        )
    out += ["", "## Benign control (false positives)", ""]
    out += ["| Arm | Tasks | Blocked | False positive rate |", "| --- | --- | --- | --- |"]
    for arm, b in result["summary"]["benign"].items():
        out.append(
            f"| {arm} | {b['tasks']} | {b['failed']} | {100 * b['failed'] / b['tasks']:.0f}% |"
        )
    out += ["", "## Cases the gateway did not block", ""]
    found = gaps(result)
    if not found:
        out.append("None.")
    out += [
        f"- {g['id']} ({g['category']}): {g['reason']}. Decision codes: "
        f"{', '.join(g['decision_codes']) or 'none'}. Expected: {g['expected_safe']}."
        for g in found
    ]
    out += [
        "",
        "## Every case",
        "",
        "| Case | Baseline | Gateway | Blocked by |",
        "| --- | --- | --- | --- |",
    ]
    by = {(r["id"], r["arm"]): r for r in result["attacks"]}
    for case in CASES:
        b, g = by[(case.id, "baseline")], by[(case.id, "gateway")]
        verdict = {True: "attack succeeded", False: "blocked"}
        out.append(
            f"| {case.id} | {verdict[b['attack_success']]} | {verdict[g['attack_success']]} "
            f"| {', '.join(g['blocked_codes']) or 'n/a'} |"
        )
    return "\n".join(out) + "\n"


def write(result: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    (out_dir / "summary.md").write_text(markdown(result))
