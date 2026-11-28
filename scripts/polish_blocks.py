#!/usr/bin/env python3
"""Count what the polish hook blocked, most frequent first.

Usage: python3 scripts/polish_blocks.py [--log PATH] [--by pattern|tool|field]
Reads the JSON lines scripts/polish_hook.py appends on every block (default: polish_blocks.jsonl in the
shared .git folder, or $POLISH_BLOCK_LOG). Prints one row per key: blocks (calls blocked, lines sharing
ts and tool) and findings (lines). Skips broken lines. Exits 0, also when nothing was recorded yet.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from polish_hook import block_log  # noqa: E402


def log(msg: str) -> None:
    print(f"[polish_blocks] {msg}", file=sys.stderr)


def read(path: Path) -> list:
    """Return the records in path; broken or incomplete lines are skipped."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and all(isinstance(row.get(k), str) for k in ("tool", "field", "pattern")):
            rows.append(row)
    return rows


def count(rows: list, by: str = "pattern") -> list:
    """Return (key, blocks, findings) sorted by findings, then blocks, then key."""
    findings = Counter(r[by] for r in rows)
    calls = {(r[by], r.get("ts", ""), r["tool"]) for r in rows}
    blocks = Counter(k for k, _, _ in calls)
    return sorted(((k, blocks[k], n) for k, n in findings.items()), key=lambda t: (-t[2], -t[1], t[0]))


def main() -> int:
    ap = argparse.ArgumentParser(description="Count polish hook blocks per pattern.")
    ap.add_argument("--log", type=Path, default=None)
    ap.add_argument("--by", choices=("pattern", "tool", "field"), default="pattern")
    args = ap.parse_args()
    path = args.log or block_log()
    rows = read(path)
    log(f"log={path} records={len(rows)} by={args.by}")
    if not rows:
        print(f"no blocks recorded in {path}")
        return 0
    table = count(rows, args.by)
    width = max(len(args.by), *(len(k) for k, _, _ in table))
    print(f"{args.by:<{width}}  blocks  findings")
    for key, blocks, findings in table:
        print(f"{key:<{width}}  {blocks:>6}  {findings:>8}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
