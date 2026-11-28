#!/usr/bin/env python3
"""Headless benchmark runner and baseline comparator for auto-ontology-mcp.

Measures deterministic quality metrics and latency across five core capabilities
using the built-in retail demo database and demo knowledge store:
  1. relationship_inference
  2. ontology_search
  3. fulltext_search
  4. check_answerable
  5. validate_sql

Every run also times hybrid ``search_ontology`` (lexical + vector) with a
deterministic hash embedder, so vector sync and cached hybrid search latency are
compared against the baseline without fastembed or a model download.

Latency is noisy on shared machines, so ``--repeats`` runs the suite several
times and reports the median of each run's p50/p95 for the baseline comparison.

With ``--semantic`` it also runs the ``semantic_search`` task set (synonym,
paraphrase and Korean-English cross-lingual queries) twice, once lexical-only
and once with the optional local hybrid search (fastembed + sqlite-vec), and
reports recall@3, MRR and latency for both modes side by side.

All runs execute inside an isolated temporary directory without touching user
state or requesting GUI focus.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
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
from auto_ontology_mcp.semantic import model_disk_bytes, normalize_vector
from auto_ontology_mcp.service import OntologyService

logger = logging.getLogger("auto_ontology_mcp.benchmark")

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
    "vector_sync",
    "hybrid_search",
)
SEMANTIC_GROUPS = ("synonym", "paraphrase", "cross_lingual")
SEMANTIC_MODES = ("lexical", "hybrid")
SEMANTIC_RECALL_K = 3
SCHEMA_VERSION = 2
HASH_EMBED_DIM = 64
# Latency is only comparable on the machine class the baseline was measured on; CI runs on
# GitHub-hosted runners, so the baseline is re-measured there by this workflow.
BASELINE_WORKFLOW = "benchmark-baseline.yml"
REFRESH_COMMAND = (
    f"gh workflow run {BASELINE_WORKFLOW} --ref <branch> && "
    "gh run download <run-id> -n baseline -D benchmarks"
)


class HashEmbedder:
    """Deterministic bag-of-words embedder: each token hashes to one of HASH_EMBED_DIM axes.

    It has no semantic quality; it exists so hybrid search (embed, vector sync, vector query,
    fusion) can be timed in CI without fastembed or a model download.
    """

    model_name = "bench-hash-v1"

    @staticmethod
    def _encode(text: str) -> list[float]:
        vec = [0.0] * HASH_EMBED_DIM
        vec[0] = 0.05  # keep every vector non-zero
        for token in (text or "").casefold().split():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest()
            vec[1 + int.from_bytes(digest, "big") % (HASH_EMBED_DIM - 1)] += 1.0
        return normalize_vector(vec)

    def embed_documents(self, texts: Any) -> list[list[float]]:
        return [self._encode(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._encode(text)


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


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def _environment() -> dict[str, Any]:
    """Where the latency was measured, so a baseline from another machine class is recognised."""
    env = os.environ
    on_actions = env.get("GITHUB_ACTIONS") == "true"
    run_url = None
    if on_actions and env.get("GITHUB_RUN_ID"):
        run_url = (
            f"{env.get('GITHUB_SERVER_URL', 'https://github.com')}/{env.get('GITHUB_REPOSITORY', '')}"
            f"/actions/runs/{env['GITHUB_RUN_ID']}/attempts/{env.get('GITHUB_RUN_ATTEMPT', '1')}"
        )
    return {
        "github_actions": on_actions,
        "os": f"{platform.system()} {platform.release()}",
        "cpu": _cpu_model(),
        "cpu_count": os.cpu_count(),
        "runner_image": f"{env['ImageOS']} {env.get('ImageVersion', '')}".strip() if env.get("ImageOS") else None,
        "runner_name": env.get("RUNNER_NAME"),
        "label": env.get("BENCHMARK_LABEL"),
        "run_url": run_url,
    }


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


def _make_service(
    root: Path,
    demo_db: Path,
    *,
    semantic: bool = False,
    embedder: Any = None,
    use_sqlite_vec: bool | None = None,
    model: str | None = None,
) -> tuple[OntologyService, float]:
    """Build an isolated service; core benchmarks stay lexical so results never depend on a model."""
    settings = replace(
        Settings.from_env(),
        home=root,
        store_path=root / "ontology.db",
        token=None,
        vault=None,
        allow_datasource_tools=True,
        semantic_search=semantic,
    )
    if model:
        settings = replace(settings, embedding_model=model)
    svc = OntologyService(settings)
    if semantic and embedder is not None:
        svc.vectors._embedder = embedder
        svc.vectors._model_name = embedder.model_name
        if use_sqlite_vec is not None:
            svc.vectors._use_sqlite_vec = use_sqlite_vec
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


def _make_hybrid_service(root: Path, demo_db: Path, setup: dict[str, Any]) -> OntologyService:
    # The plain SQLite blob backend keeps the measurement identical with or without sqlite-vec installed.
    svc, _ = _make_service(root, demo_db, semantic=True, embedder=HashEmbedder(), use_sqlite_vec=False)
    if not svc.vectors.is_available():
        raise RuntimeError("hybrid benchmark: vector index unavailable with the hash embedder")
    _apply_setup(svc, setup)
    return svc


def _hybrid_query(svc: OntologyService, item: dict[str, Any]) -> dict[str, Any]:
    out = svc.search_ontology(item["query"], kinds=item.get("kinds"), datasource=item.get("datasource"))
    if out.get("search_mode") != "hybrid":
        raise RuntimeError(f"hybrid benchmark: search ran in {out.get('search_mode')} mode")
    return out


def _reset_vectors(svc: OntologyService) -> None:
    """Drop the vector index so the next query embeds and indexes every document again."""
    svc._synced_ontology_digest.clear()
    path = svc.vectors.path
    for suffix in ("", "-wal", "-shm"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)


def _eval_hybrid_search(
    tmp_path: Path,
    demo_db: Path,
    setup: dict[str, Any],
    tasks_spec: list[dict[str, Any]],
    *,
    warmup: int,
    iterations: int,
) -> tuple[list[float], list[float]]:
    """Time hybrid search_ontology: the first query on a fresh store (full vector sync) and warm queries."""
    svc = _make_hybrid_service(tmp_path / "hybrid", demo_db, setup)
    sync_latencies: list[float] = []
    first = tasks_spec[0]
    for _ in range(iterations):
        _reset_vectors(svc)
        t0 = time.perf_counter()
        _hybrid_query(svc, first)
        sync_latencies.append((time.perf_counter() - t0) * 1000.0)
    for _ in range(warmup):
        for item in tasks_spec:
            _hybrid_query(svc, item)
    latencies: list[float] = []
    for _ in range(iterations):
        for item in tasks_spec:
            t0 = time.perf_counter()
            _hybrid_query(svc, item)
            latencies.append((time.perf_counter() - t0) * 1000.0)
    return sync_latencies, latencies


def _hit_label(hit: dict[str, Any]) -> str | None:
    return hit.get("name") or hit.get("heading") or hit.get("title")


def _run_semantic_query(svc: OntologyService, item: dict[str, Any]) -> list[dict[str, Any]]:
    if item.get("target") == "text":
        return svc.knowledge.search_text(item["query"], kinds=item.get("kinds"), limit=10)["hits"]
    return svc.search_ontology(item["query"], kinds=item.get("kinds"), datasource=item.get("datasource"))["hits"]


def _eval_semantic_mode(
    svc: OntologyService, tasks_spec: list[dict[str, Any]], *, warmup: int, iterations: int
) -> dict[str, Any]:
    """Score one search mode on the semantic task set: recall@3, MRR, per-group recall and latency."""
    # The first pass embeds and indexes every document (hybrid only); time it separately.
    t0 = time.perf_counter()
    for item in tasks_spec:
        _run_semantic_query(svc, item)
    cold_ms = (time.perf_counter() - t0) * 1000.0
    for _ in range(max(0, warmup - 1)):
        for item in tasks_spec:
            _run_semantic_query(svc, item)

    latencies: list[float] = []
    last: dict[str, list[dict[str, Any]]] = {}
    for _ in range(iterations):
        for item in tasks_spec:
            t0 = time.perf_counter()
            hits = _run_semantic_query(svc, item)
            latencies.append((time.perf_counter() - t0) * 1000.0)
            last[item["id"]] = hits

    task_results: list[dict[str, Any]] = []
    group_hits: dict[str, list[int]] = {}
    rr_all: list[float] = []
    neg_passed = neg_total = 0
    for item in tasks_spec:
        labels = [_hit_label(h) for h in last[item["id"]]]
        if item.get("expect_empty"):
            neg_total += 1
            ok = not labels
            neg_passed += int(ok)
            task_results.append({"id": item["id"], "group": "negative", "passed": ok, "actual_top3": labels[:3]})
            continue
        expected = set(item["expected_any"])
        rank = next((i for i, n in enumerate(labels[:SEMANTIC_RECALL_K], start=1) if n in expected), None)
        rr_all.append(1.0 / rank if rank else 0.0)
        group_hits.setdefault(item["group"], []).append(int(rank is not None))
        task_results.append(
            {
                "id": item["id"],
                "group": item["group"],
                "passed": rank is not None,
                "rank": rank,
                "expected_any": item["expected_any"],
                "actual_top3": labels[:SEMANTIC_RECALL_K],
            }
        )

    positives = [h for hits in group_hits.values() for h in hits]
    return {
        "search_mode": "hybrid" if svc.vectors.is_available() else "lexical",
        f"recall_at_{SEMANTIC_RECALL_K}": round(statistics.fmean(positives), 4) if positives else 1.0,
        "mrr": round(statistics.fmean(rr_all), 4) if rr_all else 1.0,
        "recall_by_group": {g: round(statistics.fmean(v), 4) for g, v in sorted(group_hits.items())},
        "negative_precision": round(neg_passed / neg_total, 4) if neg_total else 1.0,
        "passed": sum(1 for t in task_results if t["passed"]),
        "total": len(task_results),
        "cold_pass_ms": round(cold_ms, 3),
        "latency_ms": _latency_summary(latencies),
        "tasks": task_results,
    }


def run_semantic_benchmark(
    tasks_path: Path = DEFAULT_TASKS_PATH,
    *,
    warmup: int = 1,
    iterations: int = 3,
    embedder: Any = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Compare lexical-only and hybrid search on the semantic task set.

    ``model`` picks the fastembed model (a name or alias such as ``mpnet``; default: the
    configured one) and the hybrid results record its cosine floor, load time and disk size.
    ``embedder`` replaces the fastembed model (tests use a deterministic stub). Raises
    RuntimeError when hybrid search is unavailable (the ``semantic`` extra is missing).
    """
    spec = json.loads(Path(tasks_path).read_text(encoding="utf-8"))
    tasks_spec = spec["semantic_search"]
    iterations = max(1, int(iterations))
    warmup = max(0, int(warmup))
    modes: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="aom-bench-sem-") as tmp:
        tmp_path = Path(tmp)
        demo_db = build_demo_database(tmp_path / "demo.sqlite", seed=int(spec.get("seed", 7)))
        for mode in SEMANTIC_MODES:
            svc, _ = _make_service(tmp_path / mode, demo_db, semantic=mode == "hybrid", embedder=embedder, model=model)
            if mode == "hybrid" and not svc.vectors.is_available():
                raise RuntimeError(
                    "Hybrid search is unavailable: "
                    + str(svc.semantic_status().get("reason"))
                    + ". Install with `uv sync --extra semantic`."
                )
            load_ms = 0.0
            if mode == "hybrid" and embedder is None:
                # Load (and download on first use) the model outside the timed passes.
                t0 = time.perf_counter()
                svc.vectors.similarities("warm up", ["model"])
                load_ms = (time.perf_counter() - t0) * 1000.0
            _apply_setup(svc, spec["setup"])
            logger.info("semantic benchmark: running %d tasks in %s mode", len(tasks_spec), mode)
            modes[mode] = _eval_semantic_mode(svc, tasks_spec, warmup=warmup, iterations=iterations)
            if mode == "hybrid":
                modes[mode]["model"] = svc.vectors.model_name
                modes[mode]["min_similarity"] = svc.vectors.min_similarity
                modes[mode]["model_load_ms"] = round(load_ms, 1)
                disk = model_disk_bytes(svc.vectors.model_name, svc.settings.embedding_cache_dir)
                modes[mode]["model_disk_mb"] = round(disk / 1e6, 1) if disk is not None else None

    lex, hyb = modes["lexical"], modes["hybrid"]
    key = f"recall_at_{SEMANTIC_RECALL_K}"
    lex_p50 = lex["latency_ms"]["p50"]
    hyb_p50 = hyb["latency_ms"]["p50"]
    logger.info(
        "semantic benchmark: recall@%d lexical=%.4f hybrid=%.4f, p50 lexical=%.3fms hybrid=%.3fms",
        SEMANTIC_RECALL_K, lex[key], hyb[key], lex_p50, hyb_p50,
    )
    return {
        "tasks_total": len(tasks_spec),
        "recall_k": SEMANTIC_RECALL_K,
        "iterations": iterations,
        "runs": 1,
        "modes": modes,
        "delta": _semantic_delta(modes),
    }


def _semantic_delta(modes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    lex, hyb = modes["lexical"], modes["hybrid"]
    key = f"recall_at_{SEMANTIC_RECALL_K}"
    lex_ok = {t["id"]: t["passed"] for t in lex["tasks"]}
    hyb_ok = {t["id"]: t["passed"] for t in hyb["tasks"]}
    lex_p50 = lex["latency_ms"]["p50"]
    hyb_p50 = hyb["latency_ms"]["p50"]
    return {
        key: round(hyb[key] - lex[key], 4),
        "mrr": round(hyb["mrr"] - lex["mrr"], 4),
        "recall_by_group": {
            g: round(hyb["recall_by_group"].get(g, 0.0) - lex["recall_by_group"].get(g, 0.0), 4)
            for g in sorted(set(lex["recall_by_group"]) | set(hyb["recall_by_group"]))
        },
        "p50_ms": round(hyb_p50 - lex_p50, 3),
        "p50_ratio": round(hyb_p50 / lex_p50, 3) if lex_p50 > 0 else None,
        "gained": sorted(i for i, ok in hyb_ok.items() if ok and not lex_ok.get(i)),
        "lost": sorted(i for i, ok in lex_ok.items() if ok and not hyb_ok.get(i)),
    }


def _run_once(spec: dict[str, Any], *, warmup: int, iterations: int) -> dict[str, Any]:
    """Run the full headless benchmark suite once in an isolated temporary directory."""
    seed = int(spec.get("seed", 7))
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
        sync_lat, hyb_lat = _eval_hybrid_search(
            tmp_path, demo_db, spec["setup"], spec["ontology_search"], warmup=warmup, iterations=iterations
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
        "overall_score": overall,
        "scores": scores,
        "latency_ms": {
            "scan_and_infer": _latency_summary(scan_latencies),
            "ontology_search": _latency_summary(os_lat),
            "fulltext_search": _latency_summary(ft_lat),
            "check_answerable": _latency_summary(ca_lat),
            "validate_sql": _latency_summary(vs_lat),
            "vector_sync": _latency_summary(sync_lat),
            "hybrid_search": _latency_summary(hyb_lat),
            "total_suite_ms": total_suite_ms,
        },
    }


def _median_latency(runs: list[dict[str, Any]], cat: str) -> dict[str, Any]:
    """Median of each run's summary, so one run slowed by background load cannot move the p50."""
    per_run = [r["latency_ms"][cat] for r in runs]
    return {
        "mean": round(statistics.median(p["mean"] for p in per_run), 3),
        "p50": round(statistics.median(p["p50"] for p in per_run), 3),
        "p95": round(statistics.median(p["p95"] for p in per_run), 3),
        "count": sum(p["count"] for p in per_run),
        "p50_runs": [p["p50"] for p in per_run],
    }


def run_benchmark(
    tasks_path: Path = DEFAULT_TASKS_PATH,
    *,
    warmup: int = 1,
    iterations: int = 3,
    repeats: int = 1,
) -> dict[str, Any]:
    """Run the suite ``repeats`` times; quality comes from the first run, latency is the median across runs."""
    spec = json.loads(Path(tasks_path).read_text(encoding="utf-8"))
    iterations = max(1, int(iterations))
    warmup = max(0, int(warmup))
    repeats = max(1, int(repeats))

    runs: list[dict[str, Any]] = []
    for r in range(repeats):
        runs.append(_run_once(spec, warmup=warmup, iterations=iterations))
        logger.info(
            "benchmark run %d/%d: overall=%.4f hybrid_search p50=%.3fms vector_sync p50=%.3fms",
            r + 1, repeats, runs[-1]["overall_score"],
            runs[-1]["latency_ms"]["hybrid_search"]["p50"], runs[-1]["latency_ms"]["vector_sync"]["p50"],
        )
    first = runs[0]
    for r, run in enumerate(runs[1:], start=2):
        if run["overall_score"] != first["overall_score"]:
            logger.warning(
                "benchmark run %d quality differs from run 1 (%.4f vs %.4f); reporting run 1",
                r, run["overall_score"], first["overall_score"],
            )

    latency: dict[str, Any] = {cat: _median_latency(runs, cat) for cat in LATENCY_CATEGORIES}
    latency["total_suite_ms"] = round(statistics.median(r["latency_ms"]["total_suite_ms"] for r in runs), 3)
    return {
        "schema_version": SCHEMA_VERSION,
        "tasks_version": str(spec.get("version", "1")),
        "git_commit": _git_commit(),
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python_version": platform.python_version(),
        "warmup": warmup,
        "iterations": iterations,
        "repeats": repeats,
        "hybrid_embedder": HashEmbedder.model_name,
        "environments": [_environment()],
        "overall_score": first["overall_score"],
        "scores": first["scores"],
        "latency_ms": latency,
    }


def _median(values: list[float], digits: int = 3) -> float:
    return round(statistics.median(values), digits)


def merge_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine benchmark outputs from several machines: quality from the first, latency the median per machine.

    Each input is one machine's run (itself a median of its repeats), so one slow runner cannot move
    the baseline p50. ``p50_runs`` keeps every repeat of every machine.
    """
    if not results:
        raise ValueError("merge_results needs at least one benchmark result")
    first = results[0]
    for i, res in enumerate(results[1:], start=2):
        if res["overall_score"] != first["overall_score"]:
            raise ValueError(
                f"result {i} quality differs from result 1 ({res['overall_score']} vs {first['overall_score']})"
            )
    latency: dict[str, Any] = {}
    for cat in LATENCY_CATEGORIES:
        per = [r["latency_ms"][cat] for r in results]
        latency[cat] = {
            "mean": _median([p["mean"] for p in per]),
            "p50": _median([p["p50"] for p in per]),
            "p95": _median([p["p95"] for p in per]),
            "count": sum(p["count"] for p in per),
            "p50_runs": [v for p in per for v in p.get("p50_runs", [p["p50"]])],
            "p50_machines": [p["p50"] for p in per],
        }
    latency["total_suite_ms"] = _median([r["latency_ms"]["total_suite_ms"] for r in results])
    merged = {k: v for k, v in first.items() if k not in ("latency_ms", "environments", "semantic_search")}
    merged.update(
        {
            "measured_at": max(r["measured_at"] for r in results),
            "repeats": sum(r.get("repeats", 1) for r in results),
            "machines": len(results),
            "environments": [e for r in results for e in r.get("environments", [])],
            "latency_ms": latency,
        }
    )
    logger.info(
        "merged %d benchmark results: hybrid_search p50=%.3fms vector_sync p50=%.3fms",
        len(results), latency["hybrid_search"]["p50"], latency["vector_sync"]["p50"],
    )
    return merged


def merge_semantic(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine ``semantic_search`` sections from several machines: recall from the first, latency the median."""
    if not results:
        raise ValueError("merge_semantic needs at least one semantic result")
    first = results[0]
    key = f"recall_at_{first['recall_k']}"
    modes: dict[str, dict[str, Any]] = {}
    for mode in SEMANTIC_MODES:
        per = [r["modes"][mode] for r in results]
        for i, m in enumerate(per[1:], start=2):
            if m[key] != per[0][key]:
                logger.warning("semantic result %d %s %s differs (%s vs %s); keeping result 1", i, mode, key, m[key], per[0][key])
        lat = [m["latency_ms"] for m in per]
        merged_mode = dict(per[0])
        merged_mode["latency_ms"] = {
            "mean": _median([p["mean"] for p in lat]),
            "p50": _median([p["p50"] for p in lat]),
            "p95": _median([p["p95"] for p in lat]),
            "count": sum(p["count"] for p in lat),
            "p50_runs": [p["p50"] for p in lat],
        }
        merged_mode["cold_pass_ms"] = _median([m["cold_pass_ms"] for m in per])
        if mode == "hybrid" and all(m.get("model_load_ms") is not None for m in per):
            merged_mode["model_load_ms"] = _median([m["model_load_ms"] for m in per], 1)
        modes[mode] = merged_mode
    return {
        "tasks_total": first["tasks_total"],
        "recall_k": first["recall_k"],
        "iterations": first.get("iterations"),
        "runs": sum(r.get("runs", 1) for r in results),
        "modes": modes,
        "delta": _semantic_delta(modes),
    }


def _same_machine_class(baseline: dict[str, Any], current: dict[str, Any]) -> bool | None:
    """True when both sides ran on GitHub Actions (or both off it); None when a side has no record."""
    b_envs, c_envs = baseline.get("environments"), current.get("environments")
    if not b_envs or not c_envs:
        return None
    return all(e.get("github_actions") for e in b_envs) == all(e.get("github_actions") for e in c_envs)


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
        if cat not in baseline.get("latency_ms", {}) or cat not in current.get("latency_ms", {}):
            # Older snapshots predate this category; report it without judging.
            latency_diffs[cat] = {
                "baseline_p50_ms": (baseline.get("latency_ms", {}).get(cat) or {}).get("p50"),
                "current_p50_ms": (current.get("latency_ms", {}).get(cat) or {}).get("p50"),
                "delta_ms": None,
                "ratio": None,
                "regressed": False,
                "missing": True,
            }
            continue
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
    same_class = _same_machine_class(baseline, current)
    environment_note = None
    if same_class is False:
        environment_note = (
            "The baseline and this run were measured on different machine classes "
            "(GitHub Actions vs elsewhere), so latency ratios are not comparable."
        )
    refresh_hint = None
    if latency_regressions:
        slow = ", ".join(
            f"{r['category']} {r['baseline_p50_ms']:.3f} -> {r['current_p50_ms']:.3f} ms ({r['ratio']:.2f}x)"
            for r in latency_regressions
        )
        refresh_hint = (
            f"Slower than the baseline by more than {max_latency_ratio}x and {min_latency_delta_ms} ms: {slow}. "
            f"If the slowdown is intended, re-measure the baseline on GitHub runners with "
            f"`{REFRESH_COMMAND}` and commit benchmarks/baseline.json."
        )
        logger.warning("benchmark latency regression: %s", slow)

    return {
        "status": status,
        "failed": failed,
        "baseline_commit": baseline.get("git_commit"),
        "current_commit": current.get("git_commit"),
        "baseline_repeats": baseline.get("repeats", 1),
        "current_repeats": current.get("repeats", 1),
        "overall": {
            "baseline": base_overall,
            "current": curr_overall,
            "delta": overall_delta,
        },
        "quality": quality_diffs,
        "latency": latency_diffs,
        "quality_regressions": quality_regressions,
        "latency_regressions": latency_regressions,
        "same_machine_class": same_class,
        "environment_note": environment_note,
        "refresh_hint": refresh_hint,
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
    if result.get("repeats", 1) > 1:
        lines.append(f"  (median of {result['repeats']} runs)")
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
    for cat in ("vector_sync", "hybrid_search"):
        lat = result["latency_ms"][cat]
        lines.append(f"| {cat} (latency only) | | | {lat['p50']:.3f} | {lat['p95']:.3f} |")
    return "\n".join(lines)


def _format_semantic_text(sem: dict[str, Any]) -> str:
    key = f"recall_at_{sem['recall_k']}"
    lines = [f"Semantic search: lexical vs hybrid ({sem['tasks_total']} tasks, model {sem['modes']['hybrid'].get('model')})"]
    for mode in SEMANTIC_MODES:
        m = sem["modes"][mode]
        groups = "  ".join(f"{g}={m['recall_by_group'].get(g, 0.0):.2f}" for g in SEMANTIC_GROUPS)
        lines.append(
            f"  - {mode:8s} recall@{sem['recall_k']}={m[key]:.4f}  mrr={m['mrr']:.4f}  {groups}  "
            f"neg={m['negative_precision']:.2f}  p50={m['latency_ms']['p50']:.3f}ms  "
            f"p95={m['latency_ms']['p95']:.3f}ms  cold={m['cold_pass_ms']:.1f}ms"
        )
    h = sem["modes"]["hybrid"]
    if h.get("model_load_ms") is not None:
        lines.append(
            f"  - model    floor={h.get('min_similarity')}  load={h['model_load_ms']:.0f}ms  "
            f"disk={h.get('model_disk_mb') if h.get('model_disk_mb') is not None else '?'}MB"
        )
    d = sem["delta"]
    lines.append(
        f"  - delta    recall@{sem['recall_k']} {d[key]:+.4f}  mrr {d['mrr']:+.4f}  p50 {d['p50_ms']:+.3f}ms"
        + (f" ({d['p50_ratio']:.2f}x)" if d["p50_ratio"] is not None else "")
    )
    if d["gained"]:
        lines.append(f"      gained: {', '.join(d['gained'])}")
    if d["lost"]:
        lines.append(f"      lost:   {', '.join(d['lost'])}")
    return "\n".join(lines)


def _format_semantic_markdown(sem: dict[str, Any]) -> str:
    k = sem["recall_k"]
    key = f"recall_at_{k}"
    lines = [
        f"### Semantic Search: lexical vs hybrid (`{sem['modes']['hybrid'].get('model')}`)",
        "",
        f"| Mode | Recall@{k} | MRR | Synonym | Paraphrase | Cross-lingual | Negatives | p50 (ms) | p95 (ms) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for mode in SEMANTIC_MODES:
        m = sem["modes"][mode]
        g = m["recall_by_group"]
        lines.append(
            f"| {mode} | {m[key]:.4f} | {m['mrr']:.4f} | {g.get('synonym', 0.0):.2f} | {g.get('paraphrase', 0.0):.2f} | "
            f"{g.get('cross_lingual', 0.0):.2f} | {m['negative_precision']:.2f} | {m['latency_ms']['p50']:.3f} | "
            f"{m['latency_ms']['p95']:.3f} |"
        )
    d = sem["delta"]
    lines.append(f"| delta | {d[key]:+.4f} | {d['mrr']:+.4f} | | | | | {d['p50_ms']:+.3f} | |")
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
    lines.append(f"Latency p50 delta (ms, median of {cmp.get('current_repeats', 1)} runs):")
    for cat in LATENCY_CATEGORIES:
        lat = cmp["latency"][cat]
        if lat.get("missing"):
            lines.append(f"  - {cat:24s} not in both snapshots, skipped")
            continue
        flag = " [SLOWDOWN]" if lat["regressed"] else ""
        lines.append(
            f"  - {cat:24s} {lat['baseline_p50_ms']:.3f} -> {lat['current_p50_ms']:.3f} "
            f"({lat['delta_ms']:+.3f}ms, {lat['ratio']:.2f}x){flag}"
        )
    if cmp.get("environment_note"):
        lines += ["", f"Note: {cmp['environment_note']}"]
    if cmp.get("refresh_hint"):
        lines += ["", cmp["refresh_hint"]]
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
        f"| Latency (p50 ms, median of {cmp.get('current_repeats', 1)} runs) | Baseline | Current | Ratio | Status |",
        "|---|---:|---:|---:|---|",
    ]
    for cat in LATENCY_CATEGORIES:
        lat = cmp["latency"][cat]
        if lat.get("missing"):
            lines.append(f"| {cat} | - | - | - | NO BASELINE |")
            continue
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
    if cmp.get("environment_note"):
        lines += ["", f"> {cmp['environment_note']}"]
    if cmp.get("refresh_hint"):
        lines += ["", cmp["refresh_hint"]]
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
    parser.add_argument(
        "--merge",
        nargs="+",
        metavar="RESULT",
        type=Path,
        default=None,
        help="Merge benchmark JSON files from several machines (median latency) into --output without running the suite.",
    )
    parser.add_argument(
        "--merge-semantic",
        nargs="+",
        metavar="RESULT",
        type=Path,
        default=None,
        help="With --merge: take semantic_search from these --semantic runs and merge them the same way.",
    )
    parser.add_argument("--warmup", type=int, default=1, help="Warmup passes before timing.")
    parser.add_argument("--iterations", type=int, default=3, help="Timed passes per benchmark area.")
    parser.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="Run the suite this many times and compare the median latency across runs.",
    )
    parser.add_argument("--quality-tolerance", type=float, default=0.0, help="Allowed quality drop before failure.")
    parser.add_argument("--fail-on-latency", action="store_true", help="Exit 1 on latency regressions.")
    parser.add_argument("--max-latency-ratio", type=float, default=2.5, help="Max p50 latency ratio vs baseline.")
    parser.add_argument("--min-latency-delta-ms", type=float, default=10.0, help="Min p50 delta (ms) for slowdown.")
    parser.add_argument(
        "--semantic",
        action="store_true",
        help="Also compare lexical-only and hybrid (fastembed + sqlite-vec) recall and latency on the semantic task set.",
    )
    parser.add_argument(
        "--embedding-model",
        default=None,
        help="Embedding model for --semantic: a fastembed name or alias (minilm, mpnet). Default: AUTO_ONTOLOGY_EMBEDDING_MODEL or MiniLM.",
    )
    parser.add_argument("--format", choices=("text", "markdown", "json"), default="text", help="Output format.")
    parser.add_argument(
        "--summary-file",
        type=Path,
        default=None,
        help="Append the markdown comparison to this file (e.g. $GITHUB_STEP_SUMMARY in CI).",
    )
    return parser


def _append_summary(path: Path | None, cmp: dict[str, Any]) -> None:
    """Append the markdown comparison so CI shows it on the run page even when the step fails."""
    if path is None or not str(path):
        return
    with path.open("a", encoding="utf-8") as fh:
        fh.write(_format_compare_markdown(cmp) + "\n")
    logger.info("benchmark comparison summary appended to %s (status=%s)", path, cmp["status"])


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
        _append_summary(args.summary_file, cmp)
        return 1 if cmp["failed"] else 0

    if args.merge is not None:
        merged = merge_results([json.loads(p.read_text(encoding="utf-8")) for p in args.merge])
        if args.merge_semantic:
            sems = [json.loads(p.read_text(encoding="utf-8")) for p in args.merge_semantic]
            missing = [str(p) for p, s in zip(args.merge_semantic, sems) if "semantic_search" not in s]
            if missing:
                print(f"error: no semantic_search in {', '.join(missing)}", file=sys.stderr)
                return 2
            merged["semantic_search"] = merge_semantic([s["semantic_search"] for s in sems])
        text = json.dumps(merged, indent=2, ensure_ascii=False) + "\n"
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
            logger.info("merged baseline written to %s", args.output)
        else:
            print(text, end="")
        return 0

    result = run_benchmark(args.tasks, warmup=args.warmup, iterations=args.iterations, repeats=args.repeats)
    if args.semantic:
        try:
            result["semantic_search"] = run_semantic_benchmark(
                args.tasks, warmup=args.warmup, iterations=args.iterations, model=args.embedding_model
            )
        except RuntimeError as exc:
            logger.error("semantic benchmark failed: %s", exc)
            print(f"error: {exc}", file=sys.stderr)
            return 2
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
        _append_summary(args.summary_file, cmp)
        if args.semantic and args.format != "json":
            print()
            print(_format_semantic_markdown(result["semantic_search"]) if args.format == "markdown" else _format_semantic_text(result["semantic_search"]))
        return 1 if cmp["failed"] else 0

    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.format == "markdown":
        print(_format_run_markdown(result))
    else:
        print(_format_run_text(result))
    if args.semantic and args.format != "json":
        print()
        sem = result["semantic_search"]
        print(_format_semantic_markdown(sem) if args.format == "markdown" else _format_semantic_text(sem))
    return 0


if __name__ == "__main__":
    sys.exit(main())
