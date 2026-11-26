"""Tests for the fixed headless benchmark suite and baseline comparison script."""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCH_DIR = REPO_ROOT / "benchmarks"
TASKS_PATH = BENCH_DIR / "tasks.json"
SCRIPT_PATH = BENCH_DIR / "run_benchmark.py"
BASELINE_PATH = BENCH_DIR / "baseline.json"
CORE_AREAS = (
    "relationship_inference",
    "ontology_search",
    "fulltext_search",
    "check_answerable",
    "validate_sql",
)


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_benchmark", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tasks_fixture_covers_all_core_areas():
    data = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    assert data["version"] == "1"
    assert data["setup"]["seed_demo_knowledge"] is True

    for area in CORE_AREAS:
        assert area in data and data[area]

    rel = data["relationship_inference"]
    assert len(rel["expected_inferred"]) >= 6
    assert rel["expected_unlinked"]
    assert rel["expected_junctions"]

    # Every category with task lists has unique non-empty task IDs.
    seen_ids: set[str] = set()
    for section in ("expected_inferred", "expected_unlinked", "forbidden_unlinked", "expected_junctions"):
        for item in rel[section]:
            assert item["id"] and item["id"] not in seen_ids
            seen_ids.add(item["id"])
    for area in ("ontology_search", "fulltext_search", "check_answerable", "validate_sql"):
        assert len(data[area]) >= 10
        for item in data[area]:
            assert item["id"] and item["id"] not in seen_ids
            seen_ids.add(item["id"])

    # validate_sql includes both trap queries and clean queries.
    traps = [t for t in data["validate_sql"] if t.get("is_trap")]
    cleans = [t for t in data["validate_sql"] if not t.get("is_trap")]
    assert len(traps) >= 10 and len(cleans) >= 5


def test_headless_runner_measures_scores_and_latency(tmp_path: Path):
    out_file = tmp_path / "measured.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--iterations",
            "1",
            "--warmup",
            "0",
            "--output",
            str(out_file),
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    stdout_data = json.loads(proc.stdout)
    file_data = json.loads(out_file.read_text(encoding="utf-8"))
    assert stdout_data == file_data

    assert 0.0 <= file_data["overall_score"] <= 1.0
    assert set(file_data["scores"]) == set(CORE_AREAS)
    for area in CORE_AREAS:
        entry = file_data["scores"][area]
        assert 0.0 <= entry["score"] <= 1.0
        assert entry["total"] > 0

    latency = file_data["latency_ms"]
    assert latency["total_suite_ms"] > 0
    for key in ("scan_and_infer", "ontology_search", "fulltext_search", "check_answerable", "validate_sql"):
        assert latency[key]["count"] > 0
        assert latency[key]["p50"] >= 0
        assert latency[key]["p95"] >= latency[key]["p50"]


def test_baseline_file_is_valid_and_matches_current_run():
    assert BASELINE_PATH.is_file(), "benchmarks/baseline.json must exist"
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    assert baseline["schema_version"] == 1
    assert set(baseline["scores"]) == set(CORE_AREAS)
    assert baseline["overall_score"] >= 0.95

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--iterations",
            "1",
            "--warmup",
            "0",
            "--compare",
            str(BASELINE_PATH),
            "--format",
            "markdown",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Benchmark Comparison:" in proc.stdout
    assert "REGRESSED" not in proc.stdout


def test_compare_detects_quality_and_latency_regressions(tmp_path: Path):
    runner = _load_runner()
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))

    # 1. Identical snapshot passes cleanly.
    same = runner.compare_results(baseline, copy.deepcopy(baseline))
    assert same["failed"] is False
    assert same["status"] == "ok"

    # 2. Quality regression fails with exit code 1 and names the newly failing task.
    regressed_quality = copy.deepcopy(baseline)
    regressed_quality["scores"]["validate_sql"]["score"] = 0.85
    regressed_quality["scores"]["validate_sql"]["tasks"][0]["passed"] = False
    regressed_quality["overall_score"] = round(baseline["overall_score"] - 0.03, 4)

    base_file = tmp_path / "base.json"
    curr_file = tmp_path / "curr_quality.json"
    base_file.write_text(json.dumps(baseline), encoding="utf-8")
    curr_file.write_text(json.dumps(regressed_quality), encoding="utf-8")

    proc_q = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--compare-only",
            str(base_file),
            str(curr_file),
            "--format",
            "text",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc_q.returncode == 1
    assert "REGRESSED" in proc_q.stdout
    assert regressed_quality["scores"]["validate_sql"]["tasks"][0]["id"] in proc_q.stdout

    # 3. Latency regression warns by default and fails when --fail-on-latency is enabled.
    regressed_latency = copy.deepcopy(baseline)
    regressed_latency["latency_ms"]["validate_sql"]["p50"] = (
        baseline["latency_ms"]["validate_sql"]["p50"] * 4.0 + 25.0
    )
    lat_file = tmp_path / "curr_latency.json"
    lat_file.write_text(json.dumps(regressed_latency), encoding="utf-8")

    proc_warn = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--compare-only",
            str(base_file),
            str(lat_file),
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc_warn.returncode == 0
    warn_payload = json.loads(proc_warn.stdout)
    assert warn_payload["status"] == "warn"
    assert warn_payload["latency_regressions"][0]["category"] == "validate_sql"

    proc_fail = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--compare-only",
            str(base_file),
            str(lat_file),
            "--fail-on-latency",
            "--format",
            "markdown",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc_fail.returncode == 1
    assert "SLOWDOWN" in proc_fail.stdout


# ------------------------------------------------------------------ semantic (hybrid) benchmark
SEMANTIC_GROUPS = ("synonym", "paraphrase", "cross_lingual")


class _StubEmbedder:
    """Deterministic multilingual stub: Korean and English words share concept axes."""

    _AXES = (
        ("customer", "client", "고객"),
        ("employee", "staff", "직원"),
        ("city", "도시"),
        ("product", "제품"),
        ("chair", "의자", "wobbl", "흔들"),
        ("headphone", "cushion", "헤드폰", "이어"),
    )
    model_name = "stub-multilingual-v1"

    def _encode(self, text: str, *, query: bool = False) -> list[float]:
        low = (text or "").casefold()
        # Separate small document/query axes keep every vector non-zero (sqlite-vec has no cosine for
        # zero vectors) without making unrelated queries look similar to unrelated documents.
        vec = [float(sum(kw in low for kw in axis)) for axis in self._AXES]
        vec += [0.0, 0.05] if query else [0.05, 0.0]
        norm = sum(x * x for x in vec) ** 0.5
        return [x / norm for x in vec] if norm else vec

    def embed_documents(self, texts):
        return [self._encode(t) for t in texts]

    def embed_query(self, text):
        return self._encode(text, query=True)


def _has_korean(text: str) -> bool:
    return any("가" <= ch <= "힣" for ch in text)


def test_semantic_task_set_covers_synonym_paraphrase_and_cross_lingual():
    data = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    tasks = data["semantic_search"]
    other_ids = {item["id"] for area in CORE_AREAS[1:] for item in data[area]}
    ids = [t["id"] for t in tasks]
    assert len(ids) == len(set(ids)) and not (set(ids) & other_ids)
    by_group: dict[str, list[dict]] = {}
    for t in tasks:
        assert t["target"] in ("ontology", "text")
        by_group.setdefault(t["group"], []).append(t)
        if not t.get("expect_empty"):
            assert t["expected_any"]
    for group in SEMANTIC_GROUPS:
        assert len(by_group.get(group, [])) >= 5, group
    assert all(_has_korean(t["query"]) for t in by_group["cross_lingual"])
    assert by_group.get("negative")


def test_semantic_benchmark_compares_lexical_and_hybrid_modes():
    runner = _load_runner()
    sem = runner.run_semantic_benchmark(TASKS_PATH, warmup=0, iterations=1, embedder=_StubEmbedder())
    lex, hyb = sem["modes"]["lexical"], sem["modes"]["hybrid"]
    assert lex["search_mode"] == "lexical" and hyb["search_mode"] == "hybrid"
    for mode in (lex, hyb):
        assert set(mode["recall_by_group"]) == set(SEMANTIC_GROUPS)
        assert mode["latency_ms"]["count"] == sem["tasks_total"]
        assert mode["latency_ms"]["p95"] >= mode["latency_ms"]["p50"] >= 0
        assert mode["negative_precision"] == 1.0
    assert lex["recall_by_group"]["cross_lingual"] == 0.0
    assert hyb["recall_by_group"]["cross_lingual"] > 0.0
    assert sem["delta"]["recall_at_3"] > 0 and not sem["delta"]["lost"]
    assert "sem_ko_employee_list" in sem["delta"]["gained"]


def test_semantic_flag_reports_and_default_run_stays_lexical(monkeypatch, capsys):
    runner = _load_runner()
    monkeypatch.setattr(runner, "run_semantic_benchmark", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no extra")))
    assert runner.main(["--semantic", "--iterations", "1", "--warmup", "0"]) == 2
    assert "no extra" in capsys.readouterr().err

    calls: list[dict] = []
    monkeypatch.setattr(runner, "run_semantic_benchmark", lambda *a, **k: calls.append(k))
    assert runner.main(["--iterations", "1", "--warmup", "0", "--format", "json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "semantic_search" not in out and not calls
