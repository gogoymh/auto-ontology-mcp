"""Run the --semantic benchmark once and exit 0 only when hybrid search meets the bar.

Usage: uv run --extra semantic python scripts/check_semantic_recall.py [--min-recall 0.84] [--task ID ...] [--model mpnet]
Fails when hybrid recall@3 is not above --min-recall, negative precision is below 1.0,
or any --task listed does not pass in hybrid mode.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benchmarks"))

from run_benchmark import run_semantic_benchmark  # noqa: E402

logger = logging.getLogger("check_semantic_recall")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-recall", type=float, default=0.84)
    parser.add_argument("--task", action="append", default=[], help="Task id that must pass in hybrid mode.")
    parser.add_argument("--model", default=None, help="Embedding model name or alias (minilm, mpnet); default: the configured one.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    hybrid = run_semantic_benchmark(warmup=0, iterations=1, model=args.model)["modes"]["hybrid"]
    recall, negatives = hybrid["recall_at_3"], hybrid["negative_precision"]
    passed = {t["id"]: t["passed"] for t in hybrid["tasks"]}
    failures = []
    if recall <= args.min_recall:
        failures.append(f"hybrid recall@3 {recall} is not above {args.min_recall}")
    if negatives < 1.0:
        failures.append(f"negative precision {negatives} is below 1.0")
    failures += [f"task {t} did not pass" for t in args.task if not passed.get(t)]
    logger.info(
        "model=%s floor=%s load=%sms disk=%sMB p50=%sms hybrid recall@3=%s negatives=%s groups=%s",
        hybrid["model"], hybrid["min_similarity"], hybrid["model_load_ms"], hybrid["model_disk_mb"],
        hybrid["latency_ms"]["p50"], recall, negatives, hybrid["recall_by_group"],
    )
    for failure in failures:
        logger.error(failure)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
