"""The --semantic benchmark scores check_answerable on Korean phrases in both search modes."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "benchmarks"))

import run_benchmark as rb  # noqa: E402

from auto_ontology_mcp.semantic import answerable_margin  # noqa: E402

KEYS = ("precision", "recall", "true_positives", "false_positives", "positives", "negatives", "english_score")


def test_semantic_benchmark_reports_answerable_precision_and_recall_per_mode():
    sem = rb.run_semantic_benchmark(embedder=rb.HashEmbedder(), warmup=0, iterations=1)
    for mode in rb.SEMANTIC_MODES:
        ca = sem["modes"][mode]["check_answerable"]
        assert all(k in ca for k in KEYS)
        assert ca["positives"] == 28 and ca["negatives"] == 25
        assert 0.0 <= ca["precision"] <= 1.0 and 0.0 <= ca["recall"] <= 1.0
        assert len(ca["tasks"]) == 53
    assert sem["modes"]["hybrid"]["answerable_margin"] == answerable_margin(sem["modes"]["hybrid"]["model"])
    # Lexical mode covers only the phrase stored as a synonym ("고객" on Customer), and never wrongly.
    lex = sem["modes"]["lexical"]["check_answerable"]
    assert lex["precision"] == 1.0 and lex["true_positives"] >= 1 and lex["english_score"] == 1.0

    text = rb._format_semantic_text(sem)
    assert "lexical  check_answerable precision=" in text and "hybrid   check_answerable precision=" in text
    md = rb._format_semantic_markdown(sem)
    assert "| Mode | check_answerable precision | Recall | English tasks |" in md


def test_answerable_benchmark_runs_one_mode():
    res = rb.run_answerable_benchmark(embedder=rb.HashEmbedder())
    assert res["search_mode"] == "hybrid" and res["model"] == "bench-hash-v1"
    assert res["answerable_margin"] == 0.40 and res["positives"] == 28
