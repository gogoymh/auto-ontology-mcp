"""The committed baseline comes from GitHub-hosted runners and the README quotes its numbers."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "benchmarks" / "run_benchmark.py"
BASELINE = REPO / "benchmarks" / "baseline.json"
WORKFLOW = REPO / ".github" / "workflows" / "benchmark-baseline.yml"
README = REPO / "README.md"


def _runner():
    spec = importlib.util.spec_from_file_location("run_benchmark_baseline", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _baseline() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def test_baseline_measured_on_github_runners():
    base = _baseline()
    envs = base["environments"]
    assert len(envs) == 3 and base["machines"] == 3
    assert all(e["github_actions"] for e in envs)
    assert len({e["label"] for e in envs}) == 3
    for e in envs:
        assert re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+/actions/runs/\d+/attempts/\d+", e["run_url"])
    sem = base["semantic_search"]
    assert sem["iterations"] == 10 and sem["runs"] == 3
    assert len(sem["modes"]["hybrid"]["latency_ms"]["p50_runs"]) == 3


def test_readme_hybrid_latency_matches_runner_baseline():
    sem = _baseline()["semantic_search"]
    lex_p50 = sem["modes"]["lexical"]["latency_ms"]["p50"]
    hyb_p50 = sem["modes"]["hybrid"]["latency_ms"]["p50"]
    line = next(ln for ln in README.read_text(encoding="utf-8").splitlines() if "Search is lexical by default" in ln)
    m = re.search(r"query latency from p50 ([0-9.]+) ms to ([0-9.]+) ms", line)
    assert m, line
    assert (float(m.group(1)), float(m.group(2))) == (round(lex_p50, 1), round(hyb_p50, 1))
    r = re.search(r"recall@3 on synonym, paraphrase and Korean-English queries from ([0-9.]+) to ([0-9.]+)", line)
    assert r, line
    assert (float(r.group(1)), float(r.group(2))) == (
        round(sem["modes"]["lexical"]["recall_at_3"], 2),
        round(sem["modes"]["hybrid"]["recall_at_3"], 2),
    )
    assert "GitHub" in line and "load average" not in line


def _fake(runner, p50: float, label: str) -> dict:
    lat = {c: {"mean": p50, "p50": p50, "p95": p50 * 2, "count": 5, "p50_runs": [p50]} for c in runner.LATENCY_CATEGORIES}
    lat["total_suite_ms"] = p50 * 100
    return {
        "schema_version": 2,
        "measured_at": f"2026-11-28T00:00:0{int(p50) % 10}+00:00",
        "repeats": 1,
        "overall_score": 1.0,
        "scores": {c: {"score": 1.0, "tasks": []} for c in runner.SCORE_CATEGORIES},
        "environments": [{"github_actions": True, "label": label}],
        "latency_ms": lat,
    }


def _fake_sem(p50_lex: float, p50_hyb: float) -> dict:
    def mode(p50, recall):
        return {
            "recall_at_3": recall, "mrr": recall, "recall_by_group": {"synonym": recall}, "negative_precision": 1.0,
            "tasks": [{"id": "t1", "passed": recall > 0.5}], "cold_pass_ms": p50 * 10, "model_load_ms": 100.0,
            "latency_ms": {"mean": p50, "p50": p50, "p95": p50 * 2, "count": 10},
        }
    return {"tasks_total": 1, "recall_k": 3, "iterations": 10, "runs": 1,
            "modes": {"lexical": mode(p50_lex, 0.4), "hybrid": mode(p50_hyb, 0.96)}}


def test_merge_takes_median_per_runner():
    runner = _runner()
    merged = runner.merge_results([_fake(runner, 5.0, "a"), _fake(runner, 500.0, "b"), _fake(runner, 6.0, "c")])
    assert merged["machines"] == 3 and merged["repeats"] == 3
    assert merged["latency_ms"]["hybrid_search"]["p50"] == 6.0
    assert merged["latency_ms"]["hybrid_search"]["p50_runs"] == [5.0, 500.0, 6.0]
    assert [e["label"] for e in merged["environments"]] == ["a", "b", "c"]

    sem = runner.merge_semantic([_fake_sem(3.0, 10.0), _fake_sem(9.0, 90.0), _fake_sem(4.0, 12.0)])
    assert sem["runs"] == 3 and sem["iterations"] == 10
    assert sem["modes"]["lexical"]["latency_ms"]["p50"] == 4.0
    assert sem["modes"]["hybrid"]["latency_ms"]["p50"] == 12.0
    assert sem["delta"]["p50_ms"] == 8.0 and sem["delta"]["gained"] == ["t1"]


def test_merge_cli_writes_baseline(tmp_path: Path):
    runner = _runner()
    paths, sems = [], []
    for i, p50 in enumerate((5.0, 7.0, 6.0)):
        p = tmp_path / f"default-{i}.json"
        p.write_text(json.dumps(_fake(runner, p50, str(i))), encoding="utf-8")
        paths.append(str(p))
        s = tmp_path / f"semantic-{i}.json"
        s.write_text(json.dumps({"semantic_search": _fake_sem(p50, p50 * 3)}), encoding="utf-8")
        sems.append(str(s))
    out = tmp_path / "baseline.json"
    assert runner.main(["--merge", *paths, "--merge-semantic", *sems, "--output", str(out)]) == 0
    merged = json.loads(out.read_text(encoding="utf-8"))
    assert merged["latency_ms"]["validate_sql"]["p50"] == 6.0
    assert merged["semantic_search"]["modes"]["hybrid"]["latency_ms"]["p50"] == 18.0


def test_baseline_workflow_measures_three_runners_and_uploads_baseline():
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    measure = jobs["measure"]
    assert measure["runs-on"] == "ubuntu-latest"
    assert measure["strategy"]["matrix"]["runner"] == [1, 2, 3]
    runs = [" ".join(str(s.get("run", "")).split()) for s in measure["steps"]]
    default = next(i for i, r in enumerate(runs) if "run_benchmark.py --repeats 5 --output" in r)
    semantic = next(i for i, r in enumerate(runs) if "run_benchmark.py --semantic --iterations 10" in r)
    install_semantic = next(i for i, r in enumerate(runs) if r == "uv sync --extra semantic")
    assert default < install_semantic < semantic  # the default mode runs without the semantic extra, as in ci

    merge = jobs["merge"]
    assert merge["needs"] == "measure"
    merge_runs = [" ".join(str(s.get("run", "")).split()) for s in merge["steps"]]
    cmd = next(r for r in merge_runs if "--merge " in r)
    assert cmd.count("out/default-") == 3 and cmd.count("out/semantic-") == 3
    assert "--output benchmarks/baseline.json" in cmd
    upload = [s for s in merge["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact")]
    assert upload and upload[-1]["with"] == {"name": "baseline", "path": "benchmarks/baseline.json"}
