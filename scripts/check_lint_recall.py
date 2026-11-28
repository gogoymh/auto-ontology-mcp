#!/usr/bin/env python3
"""Show that the AOM-49 samples (s31-s43) were misses of the lint before AOM-49.

Usage: python3 scripts/check_lint_recall.py [--base COMMIT] [--min-missed N]
Loads scripts/lint_report.py as of COMMIT (default d4c5892, the AOM-48 merge) with git show,
runs it on each new sample and counts the samples where it misses an expected finding.
Exits 0 when at least N (default 10) were missed then and the current lint reports all of them.
"""
import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NEW = {f"s{i}" for i in range(31, 44)}


def log(msg: str) -> None:
    print(f"[check_lint_recall] {msg}", file=sys.stderr)


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="d4c5892")
    ap.add_argument("--min-missed", type=int, default=10)
    args = ap.parse_args()
    src = subprocess.run(
        ["git", "show", f"{args.base}:scripts/lint_report.py"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    with tempfile.TemporaryDirectory() as tmp:
        old_path = Path(tmp) / "lint_report_old.py"
        old_path.write_text(src, encoding="utf-8")
        old = load(old_path, "lint_report_old")
    new = load(ROOT / "scripts/lint_report.py", "lint_report_new")
    samples = [json.loads(line) for line in (ROOT / "tests/data/lint_samples.jsonl").read_text("utf-8").splitlines()]
    samples = [s for s in samples if s["id"] in NEW]
    missed = 0
    ok = len(samples) == len(NEW)
    for s in samples:
        want = set(s["kinds"])
        before = want - {k for k, _ in old.lint(s["text"])}
        now = want - {k for k, _ in new.lint(s["text"])}
        missed += bool(before)
        ok &= not now
        print(f"{s['id']} want={sorted(want)} missed_before={sorted(before)} missed_now={sorted(now)}")
    log(f"base={args.base} samples={len(samples)} missed_before={missed} all_found_now={ok}")
    return 0 if ok and missed >= args.min_missed else 1


if __name__ == "__main__":
    sys.exit(main())
