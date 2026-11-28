#!/usr/bin/env python3
"""Measure the polish lint's false-positive rate on past posts, grading documents, issue bodies and criteria.

Usage: python3 scripts/measure_lint_fp.py [--lint PATH] [--max-fp-rate R] [--keep-tp] [--source LIST]

tests/data/lint_reports.jsonl holds the texts (one JSON object per line: id, text, and source: doc, issue or
criterion; channel posts and issue comments have no source and count as post). scripts/import_lint_corpus.py adds them.
tests/data/lint_labels.json gives every (text id, pattern id) the lint flags a verdict: tp or fp. A label with an
"excerpt" applies to that one finding only, for a long text where findings of one pattern differ.
--source post,doc measures only those kinds of text.
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


def verdict(labels: dict[tuple, str], f: tuple[str, str, str]) -> str:
    """The excerpt label of finding f, else its (text id, pattern id) label, else 'unlabeled'."""
    return labels.get(f) or labels.get(f[:2]) or "unlabeled"


def measure(lint, posts: list[dict], labels: dict[tuple, str]) -> dict:
    """Count findings per pattern and verdict. A finding is (text id, pattern id, excerpt)."""
    findings = [(p["id"], kind, exc) for p in posts for kind, exc in lint(p["text"])]
    counts: Counter = Counter()
    unlabeled = sorted({f[:2] for f in findings if verdict(labels, f) == "unlabeled"})
    for f in findings:
        counts[(f[1], verdict(labels, f))] += 1
    ids = {p["id"] for p in posts}
    flagged = set(findings) | {f[:2] for f in findings}
    lost = sorted(k[:2] for k, v in labels.items() if v == "tp" and k[0] in ids and k not in flagged)
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
    ap.add_argument("--source", default="", help="comma list of post, doc, issue, criterion (default: all)")
    args = ap.parse_args()

    posts = [
        json.loads(line)
        for line in CORPUS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.source:
        keep = set(args.source.split(","))
        posts = [p for p in posts if p.get("source", "post") in keep]
    labels = {
        (r["id"], r["kind"], r["excerpt"]) if "excerpt" in r else (r["id"], r["kind"]): r["verdict"]
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
        f"lint={args.lint} source={args.source or 'all'} posts={len(posts)} findings={r['total']} fp={r['fp']} unlabeled={len(r['unlabeled'])} lost_tp={len(r['lost'])}"
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
