"""Benchmark latency is a median across repeated runs and covers hybrid search without fastembed."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from auto_ontology_mcp import semantic

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "benchmarks" / "run_benchmark.py"
BASELINE_PATH = REPO_ROOT / "benchmarks" / "baseline.json"
HYBRID_CATS = ("vector_sync", "hybrid_search")


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_benchmark_hybrid", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_run(runner, p50: float, score: float = 1.0) -> dict:
    lat = {cat: {"mean": p50, "p50": p50, "p95": p50 * 2, "count": 10} for cat in runner.LATENCY_CATEGORIES}
    lat["total_suite_ms"] = p50 * 100
    return {"overall_score": score, "scores": {c: {"score": score, "tasks": []} for c in runner.SCORE_CATEGORIES}, "latency_ms": lat}


def test_median_of_repeats_ignores_one_slow_run(monkeypatch):
    runner = _load_runner()
    runs = iter([_fake_run(runner, 5.0), _fake_run(runner, 500.0), _fake_run(runner, 6.0)])
    monkeypatch.setattr(runner, "_run_once", lambda spec, **kw: next(runs))
    result = runner.run_benchmark(repeats=3, warmup=0, iterations=1)

    assert result["repeats"] == 3 and result["schema_version"] == 2
    for cat in runner.LATENCY_CATEGORIES:
        lat = result["latency_ms"][cat]
        assert lat["p50"] == 6.0 and lat["p95"] == 12.0
        assert lat["p50_runs"] == [5.0, 500.0, 6.0]
        assert lat["count"] == 30

    baseline = copy.deepcopy(result)
    cmp = runner.compare_results(baseline, result, fail_on_latency=True)
    assert cmp["status"] == "ok" and not cmp["latency_regressions"]
    assert cmp["current_repeats"] == 3


def test_cli_repeats_flag_reaches_the_runner(monkeypatch, capsys):
    runner = _load_runner()
    seen: list[int] = []

    def fake_run_once(spec, **kw):
        seen.append(1)
        return _fake_run(runner, 4.0)

    monkeypatch.setattr(runner, "_run_once", fake_run_once)
    assert runner.main(["--repeats", "4", "--warmup", "0", "--iterations", "1", "--format", "json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(seen) == 4 and out["repeats"] == 4


def test_hybrid_latency_runs_without_fastembed(monkeypatch):
    runner = _load_runner()
    monkeypatch.setattr(semantic, "fastembed_available", lambda: False)
    monkeypatch.setattr(semantic, "sqlite_vec_available", lambda: False)

    def no_model(*a, **k):
        raise AssertionError("benchmark must not load fastembed")

    monkeypatch.setattr(semantic, "FastEmbedder", no_model)
    result = runner.run_benchmark(warmup=0, iterations=1, repeats=1)

    assert result["hybrid_embedder"] == "bench-hash-v1"
    assert result["overall_score"] == 1.0  # core scores stay lexical and unaffected
    tasks = json.loads((REPO_ROOT / "benchmarks" / "tasks.json").read_text(encoding="utf-8"))
    assert result["latency_ms"]["hybrid_search"]["count"] == len(tasks["ontology_search"])
    assert result["latency_ms"]["vector_sync"]["count"] == 1
    for cat in HYBRID_CATS:
        assert result["latency_ms"][cat]["p50"] > 0


def test_hybrid_query_refuses_lexical_fallback(tmp_path):
    runner = _load_runner()

    class Lexical:
        def search_ontology(self, *a, **k):
            return {"search_mode": "lexical", "hits": []}

    with pytest.raises(RuntimeError, match="lexical"):
        runner._hybrid_query(Lexical(), {"query": "revenue"})


@pytest.mark.parametrize("cat", HYBRID_CATS)
def test_compare_flags_hybrid_slowdown(cat):
    runner = _load_runner()
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    slow = copy.deepcopy(baseline)
    slow["latency_ms"][cat]["p50"] = baseline["latency_ms"][cat]["p50"] * 4.0 + 25.0

    warn = runner.compare_results(baseline, slow)
    assert warn["status"] == "warn"
    assert [r["category"] for r in warn["latency_regressions"]] == [cat]
    assert runner.compare_results(baseline, slow, fail_on_latency=True)["failed"] is True
    assert "SLOWDOWN" in runner._format_compare_markdown(warn)


def test_compare_skips_hybrid_against_older_baseline():
    runner = _load_runner()
    current = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    old = copy.deepcopy(current)
    for cat in HYBRID_CATS:
        del old["latency_ms"][cat]
    cmp = runner.compare_results(old, current, fail_on_latency=True)
    assert cmp["status"] == "ok"
    for cat in HYBRID_CATS:
        assert cmp["latency"][cat]["missing"] is True
    assert "NO BASELINE" in runner._format_compare_markdown(cmp)
    assert "skipped" in runner._format_compare_text(cmp)


def test_baseline_file_has_hybrid_latency_rows():
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    assert baseline["schema_version"] == 2
    assert baseline["repeats"] >= 3
    for cat in HYBRID_CATS:
        lat = baseline["latency_ms"][cat]
        assert lat["p50"] > 0 and len(lat["p50_runs"]) == baseline["repeats"]
