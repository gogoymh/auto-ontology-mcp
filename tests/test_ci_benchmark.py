"""CI runs the benchmark against the baseline and writes the comparison to the step summary."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
CI_PATH = REPO / ".github" / "workflows" / "ci.yml"
SCRIPT = REPO / "benchmarks" / "run_benchmark.py"
BASELINE = REPO / "benchmarks" / "baseline.json"
COMPARE_CMD = "benchmarks/run_benchmark.py --compare benchmarks/baseline.json"


def _benchmark_step() -> tuple[list[dict], int]:
    steps = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))["jobs"]["test"]["steps"]
    idx = next(i for i, s in enumerate(steps) if COMPARE_CMD in " ".join(str(s.get("run", "")).split()))
    return steps, idx


def test_ci_runs_benchmark_compare_step():
    steps, idx = _benchmark_step()
    step = steps[idx]
    run = " ".join(step["run"].split())
    assert '--summary-file "$GITHUB_STEP_SUMMARY"' in run
    assert "--repeats 5" in run  # latency is compared as a median across runs
    # The baseline is measured on GitHub runners too, so a p50 slowdown past the bar fails CI.
    assert "--fail-on-latency --max-latency-ratio 2.5 --min-latency-delta-ms 2" in run
    assert "continue-on-error" not in step
    sync = next(i for i, s in enumerate(steps) if "uv sync" in str(s.get("run", "")))
    assert idx > sync


def _ci_latency_flags() -> list[str]:
    """The latency flags exactly as the ci compare step passes them."""
    steps, idx = _benchmark_step()
    words = steps[idx]["run"].split()
    flags = []
    for i, w in enumerate(words):
        if w == "--fail-on-latency":
            flags.append(w)
        elif w in ("--max-latency-ratio", "--min-latency-delta-ms"):
            flags += [w, words[i + 1]]
    return flags


def _compare_only(
    tmp_path: Path, current: dict, summary: Path, *, baseline: Path = BASELINE, extra: list[str] | None = None
) -> subprocess.CompletedProcess:
    curr = tmp_path / "current.json"
    curr.write_text(json.dumps(current), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--compare-only", str(baseline), str(curr), "--summary-file", str(summary), *(extra or [])],
        capture_output=True,
        text=True,
        cwd=REPO,
        env={**os.environ, "PYTHONPATH": str(REPO / "src")},
    )


def test_quality_regression_fails_and_writes_summary(tmp_path: Path):
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    summary = tmp_path / "summary.md"
    summary.write_text("previous step\n", encoding="utf-8")

    ok = _compare_only(tmp_path, copy.deepcopy(baseline), summary)
    assert ok.returncode == 0, ok.stderr
    text = summary.read_text(encoding="utf-8")
    assert text.startswith("previous step\n")  # appended, not overwritten
    assert "### Benchmark Comparison: `OK`" in text

    bad = copy.deepcopy(baseline)
    bad["scores"]["validate_sql"]["score"] = 0.5
    bad["scores"]["validate_sql"]["tasks"][0]["passed"] = False
    bad["overall_score"] = round(baseline["overall_score"] - 0.1, 4)
    failed = _compare_only(tmp_path, bad, summary)
    assert failed.returncode == 1
    text = summary.read_text(encoding="utf-8")
    assert "### Benchmark Comparison: `REGRESSED`" in text
    assert "| validate_sql |" in text and "REGRESSED |" in text
    assert "Reproduce: `uv run python benchmarks/run_benchmark.py --compare benchmarks/baseline.json`" in text


def _slowed(cat: str, p50: float) -> dict:
    cur = json.loads(BASELINE.read_text(encoding="utf-8"))
    cur["latency_ms"][cat]["p50"] = p50
    return cur


def test_latency_gate_fails_past_2_5x_and_2ms(tmp_path: Path):
    flags = _ci_latency_flags()
    assert flags == ["--fail-on-latency", "--max-latency-ratio", "2.5", "--min-latency-delta-ms", "2"]
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    summary = tmp_path / "summary.md"
    for cat in ("hybrid_search", "validate_sql", "vector_sync"):
        b = base["latency_ms"][cat]["p50"]
        assert _compare_only(tmp_path, _slowed(cat, b * 2.4), summary, extra=flags).returncode == 0
        slow = _compare_only(tmp_path, _slowed(cat, b * 2.6 + 2.0), summary, extra=flags)
        assert slow.returncode == 1, slow.stdout
        assert "[SLOWDOWN]" in slow.stdout and cat in slow.stdout

    # 3x slower but under 2 ms more stays a pass: sub-millisecond timings jitter by whole multiples.
    tiny = copy.deepcopy(base)
    tiny["latency_ms"]["fulltext_search"]["p50"] = 0.5
    tiny_path = tmp_path / "tiny.json"
    tiny_path.write_text(json.dumps(tiny), encoding="utf-8")
    cur = copy.deepcopy(tiny)
    cur["latency_ms"]["fulltext_search"]["p50"] = 1.5
    assert _compare_only(tmp_path, cur, summary, baseline=tiny_path, extra=flags).returncode == 0


def test_latency_failure_summary_explains_refresh(tmp_path: Path):
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    summary = tmp_path / "summary.md"
    b = base["latency_ms"]["hybrid_search"]["p50"]
    failed = _compare_only(tmp_path, _slowed("hybrid_search", b * 4 + 5), summary, extra=_ci_latency_flags())
    assert failed.returncode == 1
    text = summary.read_text(encoding="utf-8")
    assert "### Benchmark Comparison: `REGRESSED`" in text
    assert "| hybrid_search |" in text and "SLOWDOWN |" in text
    hint = next(ln for ln in text.splitlines() if ln.startswith("Slower than the baseline"))
    assert "hybrid_search" in hint and "2.5x and 2.0 ms" in hint
    assert "gh workflow run benchmark-baseline.yml" in hint and "benchmarks/baseline.json" in hint
