#!/usr/bin/env python3
"""Headless benchmark runner and baseline comparator for auto-ontology-mcp.

Measures deterministic quality metrics and latency across five core capabilities
using the built-in retail demo database and demo knowledge store:
  1. relationship_inference
  2. ontology_search
  3. fulltext_search
  4. check_answerable
  5. validate_sql

All runs execute inside an isolated temporary directory without touching user
state or requesting GUI focus.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from auto_ontology_mcp.config import Settings
from auto_ontology_mcp.demo import build_demo_database
from auto_ontology_mcp.demo_knowledge import seed_demo_knowledge
from auto_ontology_mcp.service import OntologyService

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TASKS_PATH = Path(__file__).resolve().parent / "tasks.json"
SCORE_CATEGORIES = (
    "relationship_inference",
    "ontology_search",
    "fulltext_search",
    "check_answerable",
    "validate_sql",
)
LATENCY_CATEGORIES = (
    "scan_and_infer",
    "ontology_search",
    "fulltext_search",
    "check_answerable",
    "validate_sql",
)


def _git_commit() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = (len(ordered) - 1) * p
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (idx - lo)


def _latency_summary(samples_ms: list[float]) -> dict[str, float]:
    if not samples_ms:
        return {"mean": 0.0, "p50": 0.0, "p95": 0.0, "count": 0}
    return {
        "mean": round(statistics.fmean(samples_ms), 3),
        "p50": round(_percentile(samples_ms, 0.50), 3),
        "p95": round(_percentile(samples_ms, 0.95), 3),
        "count": len(samples_ms),
    }


def _make_service(root: Path, demo_db: Path) -> tuple[OntologyService, float]:
    settings = replace(
        Settings.from_env(),
        home=root,
        store_path=root / "ontology.db",
        token=None,
        vault=None,
        allow_datasource_tools=True,
    )
    svc = OntologyService(settings)
    t0 = time.perf_counter()
    svc.add_datasource("demo", f"sqlite:///{demo_db}", description="benchmark demo")
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return svc, elapsed_ms


def _apply_setup(svc: OntologyService, setup: dict[str, Any]) -> None:
    for rel in setup.get("relationships", []):
        kwargs = {
            k: v
            for k, v in rel.items()
            if k in ("source", "target", "role", "description", "is_default")
        }
        svc.set_relationship(rel["action"], **kwargs)
    for table, terms in setup.get("terms", {}).items():
        svc.save_table_terms(table, terms)
    for attr in setup.get("sql_attributes", []):
        svc.save_sql_attribute(
            attr["term"],
            attr["name"],
            attr["expression"],
            description=attr.get("description"),
            kind=attr.get("kind", "measure"),
            status=attr.get("status"),
        )
    if setup.get("glossary"):
        svc.save_glossary(setup["glossary"])
    if setup.get("seed_demo_knowledge", True):
        seed_demo_knowledge(svc)


def _eval_relationship_inference(svc: OntologyService, spec: dict[str, Any]) -> dict[str, Any]:
    listed = svc.list_relationships(status="all")
    inferred_rows = [r for r in listed["relationships"] if r["origin"] == "inferred"]
    actual_by_pair = {(r["source"], r["target"]): r for r in inferred_rows}
    expected_rows = spec["expected_inferred"]
    expected_pairs = {(e["source"], e["target"]) for e in expected_rows}
    actual_pairs = set(actual_by_pair)

    tp = len(expected_pairs & actual_pairs)
    fp = len(actual_pairs - expected_pairs)
    fn = len(expected_pairs - actual_pairs)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    tasks: list[dict[str, Any]] = []
    role_status_hits = 0
    for exp_item in expected_rows:
        pair = (exp_item["source"], exp_item["target"])
        found = actual_by_pair.get(pair)
        ok = (
            found is not None
            and found["status"] == exp_item["status"]
            and found.get("role") == exp_item.get("role")
        )
        if ok:
            role_status_hits += 1
        tasks.append(
            {
                "id": exp_item["id"],
                "passed": ok,
                "expected": {"source": exp_item["source"], "target": exp_item["target"], "status": exp_item["status"], "role": exp_item.get("role")},
                "actual": None
                if found is None
                else {"status": found["status"], "role": found.get("role"), "containment": found.get("containment")},
            }
        )
    for src, tgt in sorted(actual_pairs - expected_pairs):
        tasks.append(
            {
                "id": f"rel_unexpected_{src}_{tgt}",
                "passed": False,
                "expected": None,
                "actual": {"source": src, "target": tgt, "status": actual_by_pair[(src, tgt)]["status"]},
            }
        )

    role_status_accuracy = role_status_hits / len(expected_rows) if expected_rows else 1.0

    unlinked = {u["column"]: u for u in listed["unlinked_id_columns"]}
    struct_passed = 0
    struct_total = 0
    for item in spec.get("expected_unlinked", []):
        struct_total += 1
        entry = unlinked.get(item["column"])
        target = item.get("must_suggest_target")
        ok = entry is not None and (target is None or target in (entry.get("value_compatible_targets") or []))
        if ok:
            struct_passed += 1
        tasks.append(
            {
                "id": item["id"],
                "passed": ok,
                "expected": {"column": item["column"], "target": target},
                "actual": None if entry is None else entry.get("value_compatible_targets"),
            }
        )
    for item in spec.get("forbidden_unlinked", []):
        struct_total += 1
        ok = item["column"] not in unlinked
        if ok:
            struct_passed += 1
        tasks.append(
            {
                "id": item["id"],
                "passed": ok,
                "expected": {"excluded": item["column"]},
                "actual": {"present": item["column"] in unlinked},
            }
        )
    for item in spec.get("expected_junctions", []):
        struct_total += 1
        desc = svc.describe_table(item["table"])
        ok = bool(desc.get("junction"))
        if ok:
            struct_passed += 1
        tasks.append(
            {
                "id": item["id"],
                "passed": ok,
                "expected": {"table": item["table"], "junction": True},
                "actual": {"junction": desc.get("junction")},
            }
        )

    structural_rate = struct_passed / struct_total if struct_total else 1.0
    score = round((f1 + role_status_accuracy + structural_rate) / 3.0, 4)
    return {
        "score": score,
        "f1": round(f1, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "role_status_accuracy": round(role_status_accuracy, 4),
        "structural_accuracy": round(structural_rate, 4),
        "passed": sum(1 for t in tasks if t["passed"]),
        "total": len(tasks),
        "tasks": tasks,
    }


def _eval_ontology_search(
    svc: OntologyService, tasks_spec: list[dict[str, Any]], *, warmup: int, iterations: int
) -> tuple[dict[str, Any], list[float]]:
    for _ in range(warmup):
        for item in tasks_spec:
            svc.search_ontology(item["query"], kinds=item.get("kinds"), datasource=item.get("datasource"))

    latencies: list[float] = []
    last_outputs: dict[str, dict[str, Any]] = {}
    for _ in range(iterations):
        for item in tasks_spec:
            t0 = time.perf_counter()
            out = svc.search_ontology(item["query"], kinds=item.get("kinds"), datasource=item.get("datasource"))
            latencies.append((time.perf_counter() - t0) * 1000.0)
            last_outputs[item["id"]] = out

    pos_rr: list[float] = []
    pos_hit1 = 0
    pos_hit3 = 0
    neg_passed = 0
    neg_total = 0
    task_scores: list[float] = []
    task_results: list[dict[str, Any]] = []

    for item in tasks_spec:
        hits = last_outputs[item["id"]]["hits"]
        names = [h.get("name") for h in hits]
        if item.get("expect_empty"):
            neg_total += 1
            ok = len(hits) == 0
            if ok:
                neg_passed += 1
            task_scores.append(1.0 if ok else 0.0)
            task_results.append(
                {"id": item["id"], "passed": ok, "expected": [], "actual_top3": names[:3]}
            )
            continue

        expected = set(item["expected_any"])
        rank = next((idx for idx, n in enumerate(names[:3], start=1) if n in expected), None)
        rr = 1.0 / rank if rank is not None else 0.0
        pos_rr.append(rr)
        if rank == 1:
            pos_hit1 += 1
        if rank is not None:
            pos_hit3 += 1
        task_scores.append(rr)
        task_results.append(
            {
                "id": item["id"],
                "passed": rank == 1,
                "rank": rank,
                "expected_any": item["expected_any"],
                "actual_top3": names[:3],
            }
        )

    pos_total = len(pos_rr)
    return (
        {
            "score": round(statistics.fmean(task_scores), 4) if task_scores else 1.0,
            "mrr": round(statistics.fmean(pos_rr), 4) if pos_rr else 1.0,
            "hit_at_1": round(pos_hit1 / pos_total, 4) if pos_total else 1.0,
            "hit_at_3": round(pos_hit3 / pos_total, 4) if pos_total else 1.0,
            "negative_precision": round(neg_passed / neg_total, 4) if neg_total else 1.0,
            "passed": sum(1 for t in task_results if t["passed"]),
            "total": len(task_results),
            "tasks": task_results,
        },
        latencies,
    )


def _eval_fulltext_search(
    svc: OntologyService, tasks_spec: list[dict[str, Any]], *, warmup: int, iterations: int
) -> tuple[dict[str, Any], list[float]]:
    k = svc.knowledge
    for _ in range(warmup):
        for item in tasks_spec:
            k.search_text(item["query"], kinds=item.get("kinds"), term=item.get("term"))

    latencies: list[float] = []
    last_outputs: dict[str, dict[str, Any]] = {}
    for _ in range(iterations):
        for item in tasks_spec:
            t0 = time.perf_counter()
            out = k.search_text(item["query"], kinds=item.get("kinds"), term=item.get("term"))
            latencies.append((time.perf_counter() - t0) * 1000.0)
            last_outputs[item["id"]] = out

    pos_rr: list[float] = []
    pos_hit1 = 0
    pos_hit3 = 0
    neg_passed = 0
    neg_total = 0
    task_scores: list[float] = []
    task_results: list[dict[str, Any]] = []

    for item in tasks_spec:
        out = last_outputs[item["id"]]
        hits = out["hits"]
        if item.get("expect_empty"):
            neg_total += 1
            ok = len(hits) == 0
            if ok:
                neg_passed += 1
            task_scores.append(1.0 if ok else 0.0)
            task_results.append({"id": item["id"], "passed": ok, "expected": [], "actual_count": len(hits)})
            continue

        field = item["expected_field"]
        expected = set(item["expected_any"])
        values = [h.get(field) for h in hits]
        rank = next((idx for idx, v in enumerate(values[:3], start=1) if v in expected), None)
        mode_ok = item.get("expected_matched") is None or out.get("matched") == item["expected_matched"]
        rr = (1.0 / rank) if (rank is not None and mode_ok) else 0.0
        pos_rr.append(rr)
        if rank == 1 and mode_ok:
            pos_hit1 += 1
        if rank is not None and mode_ok:
            pos_hit3 += 1
        task_scores.append(rr)
        task_results.append(
            {
                "id": item["id"],
                "passed": rank == 1 and mode_ok,
                "rank": rank,
                "matched": out.get("matched"),
                "expected_any": item["expected_any"],
                "actual_top3": values[:3],
            }
        )

    pos_total = len(pos_rr)
    return (
        {
            "score": round(statistics.fmean(task_scores), 4) if task_scores else 1.0,
            "mrr": round(statistics.fmean(pos_rr), 4) if pos_rr else 1.0,
            "hit_at_1": round(pos_hit1 / pos_total, 4) if pos_total else 1.0,
            "hit_at_3": round(pos_hit3 / pos_total, 4) if pos_total else 1.0,
            "negative_precision": round(neg_passed / neg_total, 4) if neg_total else 1.0,
            "passed": sum(1 for t in task_results if t["passed"]),
            "total": len(task_results),
            "tasks": task_results,
        },
        latencies,
    )


def _eval_check_answerable(
    svc: OntologyService, tasks_spec: list[dict[str, Any]], *, warmup: int, iterations: int
) -> tuple[dict[str, Any], list[float]]:
    for _ in range(warmup):
        for item in tasks_spec:
            svc.check_answerable(
                item["question"],
                entities=item.get("entities"),
                datasource=item.get("datasource"),
            )

    latencies: list[float] = []
    last_outputs: dict[str, dict[str, Any]] = {}
    for _ in range(iterations):
        for item in tasks_spec:
            t0 = time.perf_counter()
            out = svc.check_answerable(
                item["question"],
                entities=item.get("entities"),
                datasource=item.get("datasource"),
            )
            latencies.append((time.perf_counter() - t0) * 1000.0)
            last_outputs[item["id"]] = out

    verdict_hits = 0
    detail_hits = 0
    task_results: list[dict[str, Any]] = []
    for item in tasks_spec:
        out = last_outputs[item["id"]]
        verdict_ok = out["verdict"] == item["expected_verdict"]
        if verdict_ok:
            verdict_hits += 1

        by_entity = {e["entity"]: e["state"] for e in out.get("entities", [])}
        entities_ok = all(
            by_entity.get(ent) == state
            for ent, state in (item.get("expected_entity_states") or {}).items()
        )
        extracted_expected = item.get("expected_extracted_entities")
        extracted_actual = [e["entity"] for e in out.get("entities", [])]
        extracted_ok = extracted_expected is None or extracted_actual == extracted_expected

        actual_link_states = [lk["state"] for lk in out.get("links", [])]
        links_ok = all(st in actual_link_states for st in (item.get("expected_link_states") or []))

        passed = verdict_ok and entities_ok and extracted_ok and links_ok
        if passed:
            detail_hits += 1
        task_results.append(
            {
                "id": item["id"],
                "passed": passed,
                "expected_verdict": item["expected_verdict"],
                "actual_verdict": out["verdict"],
                "actual_entities": by_entity,
                "actual_link_states": actual_link_states,
            }
        )

    total = len(tasks_spec)
    return (
        {
            "score": round(detail_hits / total, 4) if total else 1.0,
            "verdict_accuracy": round(verdict_hits / total, 4) if total else 1.0,
            "detail_accuracy": round(detail_hits / total, 4) if total else 1.0,
            "passed": detail_hits,
            "total": total,
            "tasks": task_results,
        },
        latencies,
    )


def _eval_validate_sql(
    svc: OntologyService, tasks_spec: list[dict[str, Any]], *, warmup: int, iterations: int
) -> tuple[dict[str, Any], list[float]]:
    for _ in range(warmup):
        for item in tasks_spec:
            svc.validate_sql(
                item["sql"],
                datasource=item.get("datasource"),
                question=item.get("question"),
            )

    latencies: list[float] = []
    last_outputs: dict[str, dict[str, Any]] = {}
    for _ in range(iterations):
        for item in tasks_spec:
            t0 = time.perf_counter()
            out = svc.validate_sql(
                item["sql"],
                datasource=item.get("datasource"),
                question=item.get("question"),
            )
            latencies.append((time.perf_counter() - t0) * 1000.0)
            last_outputs[item["id"]] = out

    tp = 0
    fn = 0
    fp = 0
    clean_passed = 0
    clean_total = 0
    trap_total = 0
    task_results: list[dict[str, Any]] = []

    for item in tasks_spec:
        out = last_outputs[item["id"]]
        errors_text = "\n".join(out.get("errors", []))
        warnings_text = "\n".join(out.get("warnings", []))
        notes_text = "\n".join(out.get("notes", []))

        valid_ok = out["valid"] is item["expected_valid"]
        err_ok = all(sub in errors_text for sub in (item.get("expect_error_contains") or []))
        warn_ok = all(sub in warnings_text for sub in (item.get("expect_warning_contains") or []))
        note_ok = all(sub in notes_text for sub in (item.get("expect_note_contains") or []))
        no_warn_ok = (not item.get("forbid_warnings")) or (len(out.get("warnings", [])) == 0)

        passed = valid_ok and err_ok and warn_ok and note_ok and no_warn_ok
        if item.get("is_trap"):
            trap_total += 1
            if passed:
                tp += 1
            else:
                fn += 1
        else:
            clean_total += 1
            if passed:
                clean_passed += 1
            if (not out["valid"]) or bool(out.get("warnings")):
                fp += 1

        task_results.append(
            {
                "id": item["id"],
                "is_trap": bool(item.get("is_trap")),
                "passed": passed,
                "expected_valid": item["expected_valid"],
                "actual_valid": out["valid"],
                "errors": out.get("errors", []),
                "warnings": out.get("warnings", []),
            }
        )

    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    total = len(tasks_spec)
    passed_total = sum(1 for t in task_results if t["passed"])
    return (
        {
            "score": round(passed_total / total, 4) if total else 1.0,
            "trap_recall": round(recall, 4),
            "clean_pass_rate": round(clean_passed / clean_total, 4) if clean_total else 1.0,
            "precision": round(precision, 4),
            "f1": round(f1, 4),
            "passed": passed_total,
            "total": total,
            "tasks": task_results,
        },
        latencies,
    )


def run_benchmark(
    tasks_path: Path = DEFAULT_TASKS_PATH,
    *,
    warmup: int = 1,
    iterations: int = 3,
) -> dict[str, Any]:
    """Run the full headless benchmark suite in an isolated temporary directory."""
    spec = json.loads(Path(tasks_path).read_text(encoding="utf-8"))
    seed = int(spec.get("seed", 7))
    iterations = max(1, int(iterations))
    warmup = max(0, int(warmup))

    suite_start = time.perf_counter()
    scan_latencies: list[float] = []

    with tempfile.TemporaryDirectory(prefix="aom-bench-") as tmp:
        tmp_path = Path(tmp)
        demo_db = build_demo_database(tmp_path / "demo.sqlite", seed=seed)

        # Warmup and measure scan + relationship inference on fresh stores.
        for w in range(warmup):
            _make_service(tmp_path / f"scan_warmup_{w}", demo_db)

        raw_svc: OntologyService | None = None
        for i in range(iterations):
            svc_i, scan_ms = _make_service(tmp_path / f"scan_iter_{i}", demo_db)
            scan_latencies.append(scan_ms)
            raw_svc = svc_i

        assert raw_svc is not None
        rel_score = _eval_relationship_inference(raw_svc, spec["relationship_inference"])

        # Prepare modeled demo + knowledge store for search, answerability and SQL checks.
        svc, _ = _make_service(tmp_path / "modeled", demo_db)
        _apply_setup(svc, spec["setup"])

        os_score, os_lat = _eval_ontology_search(
            svc, spec["ontology_search"], warmup=warmup, iterations=iterations
        )
        ft_score, ft_lat = _eval_fulltext_search(
            svc, spec["fulltext_search"], warmup=warmup, iterations=iterations
        )
        ca_score, ca_lat = _eval_check_answerable(
            svc, spec["check_answerable"], warmup=warmup, iterations=iterations
        )
        vs_score, vs_lat = _eval_validate_sql(
            svc, spec["validate_sql"], warmup=warmup, iterations=iterations
        )

    total_suite_ms = round((time.perf_counter() - suite_start) * 1000.0, 3)
    scores = {
        "relationship_inference": rel_score,
        "ontology_search": os_score,
        "fulltext_search": ft_score,
        "check_answerable": ca_score,
        "validate_sql": vs_score,
    }
    overall = round(statistics.fmean(scores[c]["score"] for c in SCORE_CATEGORIES), 4)

    return {
        "schema_version": 1,
        "tasks_version": str(spec.get("version", "1")),
        "git_commit": _git_commit(),
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python_version": platform.python_version(),
        "warmup": warmup,
        "iterations": iterations,
        "overall_score": overall,
        "scores": scores,
        "latency_ms": {
            "scan_and_infer": _latency_summary(scan_latencies),
            "ontology_search": _latency_summary(os_lat),
            "fulltext_search": _latency_summary(ft_lat),
            "check_answerable": _latency_summary(ca_lat),
            "validate_sql": _latency_summary(vs_lat),
            "total_suite_ms": total_suite_ms,
        },
    }


def compare_results(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    quality_tolerance: float = 0.0,
    max_latency_ratio: float = 2.5,
    min_latency_delta_ms: float = 10.0,
    fail_on_latency: bool = False,
) -> dict[str, Any]:
    """Compare *current* benchmark output against *baseline* and detect regressions."""
    quality_diffs: dict[str, dict[str, Any]] = {}
    quality_regressions: list[dict[str, Any]] = []

    base_overall = float(baseline["overall_score"])
    curr_overall = float(current["overall_score"])
    overall_delta = round(curr_overall - base_overall, 4)

    for cat in SCORE_CATEGORIES:
        b_cat = baseline["scores"][cat]
        c_cat = current["scores"][cat]
        b_score = float(b_cat["score"])
        c_score = float(c_cat["score"])
        delta = round(c_score - b_score, 4)
        regressed = delta < -abs(quality_tolerance)

        b_tasks = {t["id"]: t["passed"] for t in b_cat.get("tasks", [])}
        c_tasks = {t["id"]: t["passed"] for t in c_cat.get("tasks", [])}
        newly_failing = sorted(tid for tid, ok in c_tasks.items() if not ok and b_tasks.get(tid, True))
        newly_passing = sorted(tid for tid, ok in c_tasks.items() if ok and b_tasks.get(tid) is False)

        entry = {
            "baseline": b_score,
            "current": c_score,
            "delta": delta,
            "regressed": regressed,
            "newly_failing": newly_failing,
            "newly_passing": newly_passing,
        }
        quality_diffs[cat] = entry
        if regressed:
            quality_regressions.append({"category": cat, **entry})

    if overall_delta < -abs(quality_tolerance) and not quality_regressions:
        quality_regressions.append(
            {
                "category": "overall",
                "baseline": base_overall,
                "current": curr_overall,
                "delta": overall_delta,
                "newly_failing": [],
            }
        )

    latency_diffs: dict[str, dict[str, Any]] = {}
    latency_regressions: list[dict[str, Any]] = []
    for cat in LATENCY_CATEGORIES:
        b_p50 = float(baseline["latency_ms"][cat]["p50"])
        c_p50 = float(current["latency_ms"][cat]["p50"])
        delta_ms = round(c_p50 - b_p50, 3)
        ratio = round((c_p50 / b_p50), 3) if b_p50 > 0 else (1.0 if c_p50 == 0 else float("inf"))
        regressed = ratio > max_latency_ratio and delta_ms > min_latency_delta_ms
        entry = {
            "baseline_p50_ms": b_p50,
            "current_p50_ms": c_p50,
            "delta_ms": delta_ms,
            "ratio": ratio,
            "regressed": regressed,
        }
        latency_diffs[cat] = entry
        if regressed:
            latency_regressions.append({"category": cat, **entry})

    failed = bool(quality_regressions) or (fail_on_latency and bool(latency_regressions))
    status = "regressed" if failed else ("warn" if latency_regressions else "ok")

    return {
        "status": status,
        "failed": failed,
        "baseline_commit": baseline.get("git_commit"),
        "current_commit": current.get("git_commit"),
        "overall": {
            "baseline": base_overall,
            "current": curr_overall,
            "delta": overall_delta,
        },
        "quality": quality_diffs,
        "latency": latency_diffs,
        "quality_regressions": quality_regressions,
        "latency_regressions": latency_regressions,
        "reproduce_command": "uv run python benchmarks/run_benchmark.py --compare benchmarks/baseline.json",
    }


def _format_run_text(result: dict[str, Any]) -> str:
    lines = [
        f"auto-ontology-mcp benchmark (commit {result['git_commit']}, overall={result['overall_score']:.4f})",
        "",
        "Quality scores:",
    ]
    for cat in SCORE_CATEGORIES:
        s = result["scores"][cat]
        lines.append(f"  - {cat:24s} score={s['score']:.4f} ({s['passed']}/{s['total']} passed)")
    lines.append("")
    lines.append("Latency (ms):")
    for cat in LATENCY_CATEGORIES:
        lat = result["latency_ms"][cat]
        lines.append(
            f"  - {cat:24s} p50={lat['p50']:.3f}ms  p95={lat['p95']:.3f}ms  mean={lat['mean']:.3f}ms"
        )
    lines.append(f"  - {'total_suite_ms':24s} {result['latency_ms']['total_suite_ms']:.3f}ms")
    return "\n".join(lines)


def _format_run_markdown(result: dict[str, Any]) -> str:
    lines = [
        f"### Benchmark Result (`{result['git_commit']}`)",
        "",
        f"- Overall score: **{result['overall_score']:.4f}**",
        f"- Total suite latency: **{result['latency_ms']['total_suite_ms']:.1f} ms**",
        "",
        "| Category | Score | Passed | p50 (ms) | p95 (ms) |",
        "|---|---:|---:|---:|---:|",
    ]
    lat_map = {
        "relationship_inference": "scan_and_infer",
        "ontology_search": "ontology_search",
        "fulltext_search": "fulltext_search",
        "check_answerable": "check_answerable",
        "validate_sql": "validate_sql",
    }
    for cat in SCORE_CATEGORIES:
        s = result["scores"][cat]
        lat = result["latency_ms"][lat_map[cat]]
        lines.append(
            f"| {cat} | {s['score']:.4f} | {s['passed']}/{s['total']} | {lat['p50']:.3f} | {lat['p95']:.3f} |"
        )
    return "\n".join(lines)


def _format_compare_text(cmp: dict[str, Any]) -> str:
    ov = cmp["overall"]
    lines = [
        f"Benchmark comparison: {cmp['status'].upper()} "
        f"(overall {ov['baseline']:.4f} -> {ov['current']:.4f}, delta {ov['delta']:+.4f})",
        "",
        "Quality delta:",
    ]
    for cat in SCORE_CATEGORIES:
        q = cmp["quality"][cat]
        flag = " [REGRESSED]" if q["regressed"] else ""
        lines.append(
            f"  - {cat:24s} {q['baseline']:.4f} -> {q['current']:.4f} ({q['delta']:+.4f}){flag}"
        )
        if q["newly_failing"]:
            lines.append(f"      newly failing: {', '.join(q['newly_failing'])}")
    lines.append("")
    lines.append("Latency p50 delta (ms):")
    for cat in LATENCY_CATEGORIES:
        lat = cmp["latency"][cat]
        flag = " [SLOWDOWN]" if lat["regressed"] else ""
        lines.append(
            f"  - {cat:24s} {lat['baseline_p50_ms']:.3f} -> {lat['current_p50_ms']:.3f} "
            f"({lat['delta_ms']:+.3f}ms, {lat['ratio']:.2f}x){flag}"
        )
    if cmp["failed"]:
        lines.append("")
        lines.append(f"Reproduce: {cmp['reproduce_command']}")
    return "\n".join(lines)


def _format_compare_markdown(cmp: dict[str, Any]) -> str:
    ov = cmp["overall"]
    lines = [
        f"### Benchmark Comparison: `{cmp['status'].upper()}`",
        "",
        f"- Overall score: `{ov['baseline']:.4f}` -> `{ov['current']:.4f}` (`{ov['delta']:+.4f}`)",
        f"- Commits: `{cmp.get('baseline_commit')}` -> `{cmp.get('current_commit')}`",
        "",
        "| Category | Baseline | Current | Delta | Status |",
        "|---|---:|---:|---:|---|",
    ]
    for cat in SCORE_CATEGORIES:
        q = cmp["quality"][cat]
        state = "REGRESSED" if q["regressed"] else "OK"
        lines.append(f"| {cat} | {q['baseline']:.4f} | {q['current']:.4f} | {q['delta']:+.4f} | {state} |")
    lines += [
        "",
        "| Latency (p50 ms) | Baseline | Current | Ratio | Status |",
        "|---|---:|---:|---:|---|",
    ]
    for cat in LATENCY_CATEGORIES:
        lat = cmp["latency"][cat]
        state = "SLOWDOWN" if lat["regressed"] else "OK"
        lines.append(
            f"| {cat} | {lat['baseline_p50_ms']:.3f} | {lat['current_p50_ms']:.3f} | {lat['ratio']:.2f}x | {state} |"
        )
    if cmp["quality_regressions"]:
        lines.append("")
        lines.append("Regressed tasks:")
        for reg in cmp["quality_regressions"]:
            failed_ids = ", ".join(reg.get("newly_failing") or []) or "(aggregate drop)"
            lines.append(f"- `{reg['category']}`: {failed_ids}")
    if cmp["failed"]:
        lines += ["", f"Reproduce: `{cmp['reproduce_command']}`"]
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Headless benchmark runner for auto-ontology-mcp.")
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS_PATH, help="Path to tasks.json fixture.")
    parser.add_argument("--output", type=Path, default=None, help="Write measured benchmark JSON to this path.")
    parser.add_argument("--compare", type=Path, default=None, help="Compare current run against a baseline JSON.")
    parser.add_argument(
        "--compare-only",
        nargs=2,
        metavar=("BASELINE", "CURRENT"),
        type=Path,
        default=None,
        help="Compare two saved benchmark JSON files without executing the suite.",
    )
    parser.add_argument("--warmup", type=int, default=1, help="Warmup passes before timing.")
    parser.add_argument("--iterations", type=int, default=3, help="Timed passes per benchmark area.")
    parser.add_argument("--quality-tolerance", type=float, default=0.0, help="Allowed quality drop before failure.")
    parser.add_argument("--fail-on-latency", action="store_true", help="Exit 1 on latency regressions.")
    parser.add_argument("--max-latency-ratio", type=float, default=2.5, help="Max p50 latency ratio vs baseline.")
    parser.add_argument("--min-latency-delta-ms", type=float, default=10.0, help="Min p50 delta (ms) for slowdown.")
    parser.add_argument("--format", choices=("text", "markdown", "json"), default="text", help="Output format.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.compare_only is not None:
        baseline = json.loads(args.compare_only[0].read_text(encoding="utf-8"))
        current = json.loads(args.compare_only[1].read_text(encoding="utf-8"))
        cmp = compare_results(
            baseline,
            current,
            quality_tolerance=args.quality_tolerance,
            max_latency_ratio=args.max_latency_ratio,
            min_latency_delta_ms=args.min_latency_delta_ms,
            fail_on_latency=args.fail_on_latency,
        )
        if args.format == "json":
            print(json.dumps(cmp, indent=2, ensure_ascii=False))
        elif args.format == "markdown":
            print(_format_compare_markdown(cmp))
        else:
            print(_format_compare_text(cmp))
        return 1 if cmp["failed"] else 0

    result = run_benchmark(args.tasks, warmup=args.warmup, iterations=args.iterations)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.compare is not None:
        baseline = json.loads(args.compare.read_text(encoding="utf-8"))
        cmp = compare_results(
            baseline,
            result,
            quality_tolerance=args.quality_tolerance,
            max_latency_ratio=args.max_latency_ratio,
            min_latency_delta_ms=args.min_latency_delta_ms,
            fail_on_latency=args.fail_on_latency,
        )
        if args.format == "json":
            print(json.dumps({"result": result, "comparison": cmp}, indent=2, ensure_ascii=False))
        elif args.format == "markdown":
            print(_format_compare_markdown(cmp))
        else:
            print(_format_compare_text(cmp))
        return 1 if cmp["failed"] else 0

    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.format == "markdown":
        print(_format_run_markdown(result))
    else:
        print(_format_run_text(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
