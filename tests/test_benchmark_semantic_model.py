"""End-to-end semantic benchmark with the real local model (needs the `semantic` extra)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from auto_ontology_mcp.semantic import semantic_dependencies_available

from strict_skip import REQUIRE_SEMANTIC_ENV, skip_unless_required

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "benchmarks" / "run_benchmark.py"



def require_semantic_extra() -> None:
    if not semantic_dependencies_available():
        skip_unless_required("semantic extra not installed", REQUIRE_SEMANTIC_ENV)


@pytest.fixture(autouse=True)
def _require_semantic_extra():
    require_semantic_extra()


def test_cli_semantic_flag_measures_recall_gain_and_latency(tmp_path: Path):
    out_file = tmp_path / "semantic.json"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--semantic", "--iterations", "1", "--warmup", "1",
         "--output", str(out_file), "--format", "json"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    sem = json.loads(out_file.read_text(encoding="utf-8"))["semantic_search"]
    lex, hyb = sem["modes"]["lexical"], sem["modes"]["hybrid"]
    assert hyb["recall_at_3"] > lex["recall_at_3"]
    assert hyb["recall_by_group"]["cross_lingual"] >= 0.5 > lex["recall_by_group"]["cross_lingual"]
    assert hyb["negative_precision"] == 1.0 and not sem["delta"]["lost"]
    assert hyb["latency_ms"]["p50"] > 0 and lex["latency_ms"]["p50"] > 0 and sem["delta"]["p50_ratio"] > 0
