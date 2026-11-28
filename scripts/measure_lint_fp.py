#!/usr/bin/env python3
"""Measure the polish lint's false-positive rate on past channel posts and issue comments.

Usage: python3 scripts/measure_lint_fp.py [--lint PATH] [--max-fp-rate R] [--keep-tp]

tests/data/lint_reports.jsonl holds the posts (one JSON object per line: id, author, text).
tests/data/lint_labels.json gives every (post id, pattern id) the lint flags a verdict: tp or fp.
Prints a table per pattern and exits 1 when
  - a finding has no verdict (label it before trusting the rate),
  - the false-positive rate is above --max-fp-rate, or
  - with --keep-tp, a finding labeled tp is no longer reported.
--lint points at another copy of lint_report.py, e.g. one saved with git show <commit>:scripts/lint_report.py.
"""

import argparse
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests/data/lint_reports.jsonl"
LABELS = ROOT / "tests/data/lint_labels.json"


def log(msg: str) -> None:
    print(f"[measure_lint_fp] {msg}", file=sys.stderr)


def load_lint(path: Path):
    spec = importlib.util.spec_from_file_location("lint_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.lint


def measure(lint, posts: list[dict], labels: dict[tuple[str, str], str]) -> dict:
    """Count findings per pattern and verdict. A finding is (post id, pattern id)."""
    findings = [(p["id"], kind) for p in posts for kind, _ in lint(p["text"])]
    counts: Counter = Counter()
    unlabeled = sorted({f for f in findings if f not in labels})
    for f in findings:
        counts[(f[1], labels.get(f, "unlabeled"))] += 1
    flagged = set(findings)
    lost = sorted(k for k, v in labels.items() if v == "tp" and k not in flagged)
    total = len(findings)
    fp = sum(n for (_, v), n in counts.items() if v == "fp")
    return {
        "total": total,
        "fp": fp,
        "counts": counts,
        "unlabeled": unlabeled,
        "lost": lost,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--lint", type=Path, default=ROOT / "scripts/lint_report.py")
    ap.add_argument("--max-fp-rate", type=float, default=1.0)
    ap.add_argument(
        "--keep-tp", action="store_true", help="fail when a finding labeled tp is gone"
    )
    args = ap.parse_args()

    posts = [
        json.loads(line)
        for line in CORPUS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    labels = {
        (r["id"], r["kind"]): r["verdict"]
        for r in json.loads(LABELS.read_text(encoding="utf-8"))
    }
    r = measure(load_lint(args.lint), posts, labels)

    kinds = sorted({k for k, _ in r["counts"]})
    print(f"{'pattern':<15}{'tp':>4}{'fp':>4}{'?':>4}")
    for k in kinds:
        c = r["counts"]
        print(f"{k:<15}{c[(k, 'tp')]:>4}{c[(k, 'fp')]:>4}{c[(k, 'unlabeled')]:>4}")
    rate = r["fp"] / r["total"] if r["total"] else 0.0
    print(f"posts={len(posts)} findings={r['total']} fp={r['fp']} fp_rate={rate:.1%}")
    log(
        f"lint={args.lint} posts={len(posts)} findings={r['total']} fp={r['fp']} unlabeled={len(r['unlabeled'])} lost_tp={len(r['lost'])}"
    )

    ok = True
    for pid, kind in r["unlabeled"]:
        print(f"unlabeled: {pid} {kind} -> add a verdict to {LABELS.relative_to(ROOT)}")
        ok = False
    if rate > args.max_fp_rate:
        print(f"fail: fp_rate {rate:.1%} is above {args.max_fp_rate:.1%}")
        ok = False
    if args.keep_tp:
        for pid, kind in r["lost"]:
            print(f"lost: {pid} {kind} was a true finding and is no longer reported")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
