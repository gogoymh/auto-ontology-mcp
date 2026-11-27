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
    assert "--fail-on-latency" not in run  # shared runners are noisy; only quality fails CI
    assert "continue-on-error" not in step
    sync = next(i for i, s in enumerate(steps) if "uv sync" in str(s.get("run", "")))
    assert idx > sync


def _compare_only(tmp_path: Path, current: dict, summary: Path) -> subprocess.CompletedProcess:
    curr = tmp_path / "current.json"
    curr.write_text(json.dumps(current), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--compare-only", str(BASELINE), str(curr), "--summary-file", str(summary)],
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
