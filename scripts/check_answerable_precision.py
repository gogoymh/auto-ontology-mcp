"""Score check_answerable on the Korean phrase set with one model; exit 0 only when it meets the bar.

Usage: uv run --extra semantic python scripts/check_answerable_precision.py --model mpnet [--min-recall 0.3]
Fails when precision of 'covered' is below 1.0, recall is below --min-recall, or any English
check_answerable task in benchmarks/tasks.json fails in hybrid mode.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benchmarks"))

from run_benchmark import run_answerable_benchmark  # noqa: E402

logger = logging.getLogger("check_answerable_precision")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=None, help="Embedding model name or alias (minilm, mpnet); default: the configured one.")
    parser.add_argument("--min-recall", type=float, default=0.3)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    res = run_answerable_benchmark(model=args.model)
    failures = []
    if res["precision"] < 1.0:
        failures.append(f"precision {res['precision']} is below 1.0 (false positives: {res['false_positive_phrases']})")
    if res["recall"] < args.min_recall:
        failures.append(f"recall {res['recall']} is below {args.min_recall}")
    if res["english_failed"]:
        failures.append(f"English check_answerable tasks failed: {res['english_failed']}")
    logger.info(
        "model=%s floor=%s margin=%s precision=%s recall=%s (%s/%s) english=%s in %sms",
        res["model"], res["min_similarity"], res["answerable_margin"], res["precision"], res["recall"],
        res["true_positives"], res["positives"], res["english_score"], res["elapsed_ms"],
    )
    for t in res["tasks"]:
        if not t["passed"]:
            logger.info("  miss %r: state=%s top=%s expected=%s", t["phrase"], t["state"], t["top"], t["expected"])
    for failure in failures:
        logger.error(failure)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
